import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, select, update

from app.api.dependencies import CurrentUser, DbSession
from app.api.v1.trips import (
    _cloudbase,
    _cloudbase_error,
    _ensure_user,
    _not_found,
)
from app.core.limits import MAX_TRIP_MEMBERS
from app.db.models import Trip, TripInvite, TripMember, User
from app.providers.cloudbase_pg import (
    CloudBasePgConfigurationError,
    CloudBasePgRequestError,
    CloudBasePgUnavailable,
)

router = APIRouter(prefix="/v1", tags=["trip-invites"])


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _expired() -> HTTPException:
    return HTTPException(
        status_code=410, detail={"code": "INVITE_EXPIRED", "message": "邀请已失效"}
    )


def _member_count(session: DbSession, trip_id: str) -> int:
    return (
        session.scalar(
            select(func.count(TripMember.id)).where(TripMember.trip_id == trip_id)
        )
        or 0
    )


def _placeholders(session: DbSession, trip_id: str) -> list[dict[str, Any]]:
    """The trip's unclaimed placeholders, oldest first, as an invite shows them."""
    return [
        {"id": member.user_id, "nickname": member.nickname}
        for member in session.scalars(
            select(TripMember)
            .where(TripMember.trip_id == trip_id, TripMember.is_placeholder)
            .order_by(TripMember.joined_at)
        ).all()
    ]


def _full() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "TRIP_MEMBER_LIMIT_REACHED",
            "message": f"这段旅行已满 {MAX_TRIP_MEMBERS} 人",
        },
    )


def _lock_trip(session: DbSession, trip_id: str, owner_id: str | None) -> Trip:
    """Lock the trip before invite state is trusted.

    Creating a link and joining one both take this lock and then touch
    trip_invites, matching tsb_create_trip_invite and tsb_accept_trip_invite.
    PostgreSQL locks with FOR UPDATE. SQLite drops that clause, so the first
    statement of this transaction updates the trip row and takes the writer
    lock. A caller that only looked the trip up commits that read first;
    otherwise SQLite keeps the earlier snapshot.
    """
    if session.get_bind().dialect.name == "sqlite":
        statement = update(Trip).where(Trip.id == trip_id)
        if owner_id is not None:
            statement = statement.where(Trip.owner_id == owner_id)
        session.execute(statement.values(name=Trip.name))
        trip = session.get(Trip, trip_id)
        if trip is None or (owner_id is not None and trip.owner_id != owner_id):
            raise _not_found()
        return trip
    statement = select(Trip).where(Trip.id == trip_id)
    if owner_id is not None:
        statement = statement.where(Trip.owner_id == owner_id)
    trip = session.scalar(statement.with_for_update())
    if trip is None:
        raise _not_found()
    return trip


@router.post("/trips/{trip_id}/invites", status_code=status.HTTP_201_CREATED)
async def create_trip_invite(
    trip_id: str, http_request: Request, session: DbSession, user_id: CurrentUser
) -> dict[str, dict[str, str]]:
    cloudbase = _cloudbase(http_request)
    if cloudbase is not None:
        token = secrets.token_urlsafe(32)
        expires_at = _now() + timedelta(days=7)
        try:
            result = await cloudbase.rpc(
                "tsb_create_trip_invite",
                {
                    "p_invite_id": secrets.token_urlsafe(18),
                    "p_trip_id": trip_id,
                    "p_creator_id": user_id,
                    "p_token_hash": _hash(token),
                    "p_expires_at": expires_at.isoformat(),
                },
            )
        except (
            CloudBasePgConfigurationError,
            CloudBasePgRequestError,
            CloudBasePgUnavailable,
        ) as exc:
            raise _cloudbase_error(exc) from exc
        if not isinstance(result, dict) or result.get("not_found"):
            raise _not_found()
        if result.get("full"):
            raise _full()
        return {"data": {"token": token, "expires_at": str(result["expires_at"])}}
    _lock_trip(session, trip_id, user_id)
    # A full trip still issues a link while it has a placeholder to claim;
    # joining it as a new member is what answers "full".
    if _member_count(session, trip_id) >= MAX_TRIP_MEMBERS and not _placeholders(
        session, trip_id
    ):
        raise _full()
    now = _now()
    session.execute(
        update(TripInvite)
        .where(
            TripInvite.trip_id == trip_id,
            TripInvite.used_at.is_(None),
            TripInvite.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    token = secrets.token_urlsafe(32)
    expires_at = now + timedelta(days=7)
    session.add(
        TripInvite(
            trip_id=trip_id,
            creator_id=user_id,
            token_hash=_hash(token),
            expires_at=expires_at,
        )
    )
    session.commit()
    return {"data": {"token": token, "expires_at": expires_at.isoformat()}}


@router.get("/trip-invites/{token}")
async def get_trip_invite(
    token: str, http_request: Request, session: DbSession, user_id: CurrentUser
) -> dict[str, dict[str, Any]]:
    cloudbase = _cloudbase(http_request)
    if cloudbase is not None:
        try:
            rows = await cloudbase.request(
                "GET",
                "/trip_invites",
                params={
                    "token_hash": f"eq.{_hash(token)}",
                    "select": "trip_id,expires_at,used_at,revoked_at",
                    "limit": "1",
                },
            )
        except (
            CloudBasePgConfigurationError,
            CloudBasePgRequestError,
            CloudBasePgUnavailable,
        ) as exc:
            raise _cloudbase_error(exc) from exc
        if not isinstance(rows, list) or not rows:
            raise _expired()
        invite = cast(dict[str, Any], rows[0])
        try:
            member_rows = await cloudbase.request(
                "GET",
                "/trip_members",
                params={
                    "trip_id": f"eq.{invite['trip_id']}",
                    "user_id": f"eq.{user_id}",
                    "select": "user_id",
                    "limit": "1",
                },
            )
            already_member = bool(member_rows)
            if not already_member and (
                invite.get("used_at")
                or invite.get("revoked_at")
                or str(invite["expires_at"]) <= _now().isoformat()
            ):
                raise _expired()
            trips = await cloudbase.request(
                "GET",
                "/trips",
                params={
                    "id": f"eq.{invite['trip_id']}",
                    "select": "id,name,owner_id",
                    "limit": "1",
                },
            )
            if not isinstance(trips, list) or not trips:
                raise _expired()
            cloud_trip = cast(dict[str, Any], trips[0])
            names = (
                await cloudbase.request(
                    "GET",
                    "/users",
                    params={
                        "id": f"eq.{cloud_trip.get('owner_id', '')}",
                        "select": "nickname",
                        "limit": "1",
                    },
                )
                if cloud_trip.get("owner_id")
                else []
            )
            inviter_name = (
                names[0].get("nickname")
                if isinstance(names, list) and names and isinstance(names[0], dict)
                else None
            )
            placeholder_rows = (
                []
                if already_member
                else await cloudbase.request(
                    "GET",
                    "/trip_members",
                    params={
                        "trip_id": f"eq.{invite['trip_id']}",
                        "is_placeholder": "eq.true",
                        "select": "user_id,nickname",
                        "order": "joined_at.asc",
                    },
                )
            )
            placeholders = [
                {"id": str(row["user_id"]), "nickname": row.get("nickname")}
                for row in (
                    placeholder_rows if isinstance(placeholder_rows, list) else []
                )
                if isinstance(row, dict) and "user_id" in row
            ]
        except (
            CloudBasePgConfigurationError,
            CloudBasePgRequestError,
            CloudBasePgUnavailable,
        ) as exc:
            raise _cloudbase_error(exc) from exc
        return {
            "data": {
                "trip_id": str(cloud_trip["id"]),
                "name": str(cloud_trip["name"]),
                "expires_at": str(invite["expires_at"]),
                "already_member": already_member,
                "inviter_name": inviter_name or "旅行发起人",
                "placeholders": placeholders,
            }
        }
    invite = session.scalar(
        select(TripInvite).where(TripInvite.token_hash == _hash(token))
    )
    if invite is None:
        raise _expired()
    already_member = (
        session.scalar(
            select(TripMember).where(
                TripMember.trip_id == invite.trip_id, TripMember.user_id == user_id
            )
        )
        is not None
    )
    if not already_member and (
        invite.used_at is not None
        or invite.revoked_at is not None
        or invite.expires_at <= _now()
    ):
        raise _expired()
    trip = session.get(Trip, invite.trip_id)
    if trip is None:
        raise _expired()
    return {
        "data": {
            "trip_id": trip.id,
            "name": trip.name,
            "expires_at": invite.expires_at.isoformat(),
            "already_member": already_member,
            "inviter_name": (
                user.nickname if (user := session.get(User, trip.owner_id)) else None
            )
            or "旅行发起人",
            # Who the newcomer may turn out to be; a member cannot claim.
            "placeholders": [] if already_member else _placeholders(session, trip.id),
        }
    }


@router.post("/trip-invites/{token}/accept")
async def accept_trip_invite(
    token: str, http_request: Request, session: DbSession, user_id: CurrentUser
) -> dict[str, dict[str, object]]:
    cloudbase = _cloudbase(http_request)
    if cloudbase is not None:
        try:
            result = await cloudbase.rpc(
                "tsb_accept_trip_invite",
                {"p_token_hash": _hash(token), "p_user_id": user_id},
            )
        except (
            CloudBasePgConfigurationError,
            CloudBasePgRequestError,
            CloudBasePgUnavailable,
        ) as exc:
            raise _cloudbase_error(exc) from exc
        if not isinstance(result, dict) or result.get("expired"):
            raise _expired()
        if result.get("full"):
            raise _full()
        if result.get("already_member"):
            raise HTTPException(
                status_code=409,
                detail={"code": "ALREADY_A_MEMBER", "message": "你已经加入这段旅行"},
            )
        return {"data": cast(dict[str, object], result)}
    located = session.execute(
        select(TripInvite.id, TripInvite.trip_id).where(
            TripInvite.token_hash == _hash(token)
        )
    ).one_or_none()
    if located is None:
        raise _expired()
    invite_id, trip_id = located
    # End the lookup before locking. SQLite would otherwise keep this snapshot
    # and miss a revoke that committed while the trip lock was waiting.
    session.commit()
    trip = _lock_trip(session, trip_id, None)
    invite = session.scalar(
        select(TripInvite).where(TripInvite.id == invite_id).with_for_update()
    )
    # `used_at` is only set on links consumed under the former single-use rule;
    # those stay dead. Re-read it after the trip lock, with the invite locked.
    if (
        invite is None
        or invite.used_at is not None
        or invite.revoked_at is not None
        or invite.expires_at <= _now()
    ):
        raise _expired()
    if (
        session.scalar(
            select(TripMember).where(
                TripMember.trip_id == invite.trip_id, TripMember.user_id == user_id
            )
        )
        is not None
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "ALREADY_A_MEMBER", "message": "你已经加入这段旅行"},
        )
    if _member_count(session, trip.id) >= MAX_TRIP_MEMBERS:
        raise _full()
    _ensure_user(session, user_id)
    # The link stays usable until it expires, is replaced or the trip is full.
    session.add(TripMember(trip_id=trip.id, user_id=user_id))
    session.commit()
    return {
        "data": {
            "id": trip.id,
            "name": trip.name,
            "default_currency": trip.default_currency,
            "member_count": _member_count(session, trip.id),
        }
    }
