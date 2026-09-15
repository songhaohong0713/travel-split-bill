import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.dependencies import CurrentUser, DbSession
from app.api.v1.trips import _not_found
from app.db.models import SettlementVersion, ShareLink, Trip
from app.providers.cloudbase_pg import (
    CloudBasePgClient,
    CloudBasePgConfigurationError,
    CloudBasePgUnavailable,
)

router = APIRouter(tags=["shares"])


class ShareLinkRequest(BaseModel):
    expires_in_days: int = Field(ge=1, le=30)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _cloudbase(request: Request) -> CloudBasePgClient | None:
    return getattr(request.app.state, "cloudbase_pg", None)


def _cloudbase_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=503, detail={"code": "DATABASE_UNAVAILABLE", "message": "CloudBase PostgreSQL is unavailable"})


@router.post("/v1/trips/{trip_id}/share-links", status_code=status.HTTP_201_CREATED)
async def create_share_link(
    trip_id: str, body: ShareLinkRequest, http_request: Request, session: DbSession, user_id: CurrentUser
) -> dict[str, dict[str, str]]:
    token = secrets.token_urlsafe(32)
    expires_at = _now() + timedelta(days=body.expires_in_days)
    cloudbase = _cloudbase(http_request)
    if cloudbase is not None:
        try:
            result = await cloudbase.rpc("tsb_create_share_link", {"p_link_id": str(uuid4()), "p_trip_id": trip_id, "p_owner_id": user_id, "p_token_hash": _hash(token), "p_expires_at": expires_at.isoformat()})
        except (CloudBasePgConfigurationError, CloudBasePgUnavailable) as exc:
            raise _cloudbase_error(exc) from exc
        if not isinstance(result, dict) or result.get("not_found"):
            raise _not_found()
        return {"data": {"token": token, "expires_at": str(result["expires_at"])}}
    trip = session.scalar(select(Trip).where(Trip.id == trip_id, Trip.owner_id == user_id))
    if trip is None or trip.latest_version_id is None:
        raise _not_found()
    link = ShareLink(token_hash=_hash(token), trip_id=trip_id, expires_at=expires_at)
    session.add(link)
    session.commit()
    return {"data": {"token": token, "expires_at": link.expires_at.isoformat()}}


@router.get("/public/share/{token}")
async def public_share(token: str, http_request: Request, session: DbSession) -> dict[str, object]:
    cloudbase = _cloudbase(http_request)
    if cloudbase is not None:
        try:
            links = await cloudbase.request("GET", "/share_links", params={"token_hash": f"eq.{_hash(token)}", "revoked_at": "is.null", "expires_at": f"gt.{_now().isoformat()}", "select": "trip_id", "limit": "1"})
            if not isinstance(links, list) or not links:
                raise _expired()
            link = cast(dict[str, Any], links[0])
            trips = await cloudbase.request("GET", "/trips", params={"id": f"eq.{link['trip_id']}", "latest_version_id": "not.is.null", "select": "latest_version_id", "limit": "1"})
            if not isinstance(trips, list) or not trips:
                raise _expired()
            trip_row = cast(dict[str, Any], trips[0])
            versions = await cloudbase.request("GET", "/settlement_versions", params={"id": f"eq.{trip_row['latest_version_id']}", "select": "public_json", "limit": "1"})
        except (CloudBasePgConfigurationError, CloudBasePgUnavailable) as exc:
            raise _cloudbase_error(exc) from exc
        if not isinstance(versions, list) or not versions:
            raise _expired()
        return {"data": cast(dict[str, Any], versions[0])["public_json"]}
    link = session.scalar(select(ShareLink).where(ShareLink.token_hash == _hash(token)))
    if link is None or link.revoked_at is not None or link.expires_at <= _now():
        raise _expired()
    trip = session.get(Trip, link.trip_id)
    if trip is None or trip.latest_version_id is None:
        raise _expired()
    version = session.get(SettlementVersion, trip.latest_version_id)
    if version is None:
        raise _expired()
    return {"data": version.public_json}


def _expired() -> HTTPException:
    return HTTPException(status_code=status.HTTP_410_GONE, detail={"code": "LINK_EXPIRED", "message": "链接已失效"})
