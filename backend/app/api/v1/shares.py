import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.dependencies import CurrentUser, DbSession
from app.api.v1.trips import _not_found
from app.db.models import SettlementVersion, ShareLink, Trip

router = APIRouter(tags=["shares"])


class ShareLinkRequest(BaseModel):
    expires_in_days: int = Field(ge=1, le=30)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@router.post("/v1/trips/{trip_id}/share-links", status_code=status.HTTP_201_CREATED)
def create_share_link(
    trip_id: str, body: ShareLinkRequest, session: DbSession, user_id: CurrentUser
) -> dict[str, dict[str, str]]:
    trip = session.scalar(
        select(Trip).where(Trip.id == trip_id, Trip.owner_id == user_id)
    )
    if trip is None or trip.latest_version_id is None:
        raise _not_found()
    token = secrets.token_urlsafe(32)
    link = ShareLink(
        token_hash=_hash(token),
        trip_id=trip_id,
        expires_at=_now() + timedelta(days=body.expires_in_days),
    )
    session.add(link)
    session.commit()
    return {"data": {"token": token, "expires_at": link.expires_at.isoformat()}}


@router.get("/public/share/{token}")
def public_share(token: str, session: DbSession) -> dict[str, object]:
    link = session.scalar(select(ShareLink).where(ShareLink.token_hash == _hash(token)))
    if link is None or link.revoked_at is not None or link.expires_at <= _now():
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={"code": "LINK_EXPIRED", "message": "链接已失效"},
        )
    trip = session.get(Trip, link.trip_id)
    if trip is None or trip.latest_version_id is None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={"code": "LINK_EXPIRED", "message": "链接已失效"},
        )
    version = session.get(SettlementVersion, trip.latest_version_id)
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={"code": "LINK_EXPIRED", "message": "链接已失效"},
        )
    return {"data": version.public_json}
