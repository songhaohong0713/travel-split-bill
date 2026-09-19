import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, select, update

from app.api.dependencies import CurrentUser, DbSession
from app.api.v1.trips import _cloudbase, _cloudbase_error, _ensure_user, _not_found, require_trip_member
from app.db.models import Trip, TripInvite, TripMember

router = APIRouter(prefix="/v1", tags=["trip-invites"])


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _expired() -> HTTPException:
    return HTTPException(status_code=410, detail={"code": "INVITE_EXPIRED", "message": "邀请已失效"})


def _full() -> HTTPException:
    return HTTPException(status_code=409, detail={"code": "TRIP_MEMBER_LIMIT_REACHED", "message": "这段旅行已有两位成员"})


@router.post("/trips/{trip_id}/invites", status_code=status.HTTP_201_CREATED)
async def create_trip_invite(trip_id: str, http_request: Request, session: DbSession, user_id: CurrentUser) -> dict[str, dict[str, str]]:
    if _cloudbase(http_request) is not None:
        raise _cloudbase_error(ValueError("trip invitations require CloudBase migration"))
    require_trip_member(session, trip_id, user_id)
    if session.scalar(select(func.count(TripMember.id)).where(TripMember.trip_id == trip_id)) >= 2:
        raise _full()
    now = _now()
    session.execute(update(TripInvite).where(TripInvite.trip_id == trip_id, TripInvite.used_at.is_(None), TripInvite.revoked_at.is_(None)).values(revoked_at=now))
    token = secrets.token_urlsafe(32)
    expires_at = now + timedelta(days=7)
    session.add(TripInvite(trip_id=trip_id, creator_id=user_id, token_hash=_hash(token), expires_at=expires_at))
    session.commit()
    return {"data": {"token": token, "expires_at": expires_at.isoformat()}}


@router.get("/trip-invites/{token}")
async def get_trip_invite(token: str, http_request: Request, session: DbSession, user_id: CurrentUser) -> dict[str, dict[str, str]]:
    if _cloudbase(http_request) is not None:
        raise _cloudbase_error(ValueError("trip invitations require CloudBase migration"))
    invite = session.scalar(select(TripInvite).where(TripInvite.token_hash == _hash(token)))
    if invite is None or invite.used_at is not None or invite.revoked_at is not None or invite.expires_at <= _now():
        raise _expired()
    trip = session.get(Trip, invite.trip_id)
    if trip is None:
        raise _expired()
    return {"data": {"trip_id": trip.id, "name": trip.name, "expires_at": invite.expires_at.isoformat()}}


@router.post("/trip-invites/{token}/accept")
async def accept_trip_invite(token: str, http_request: Request, session: DbSession, user_id: CurrentUser) -> dict[str, dict[str, object]]:
    if _cloudbase(http_request) is not None:
        raise _cloudbase_error(ValueError("trip invitations require CloudBase migration"))
    invite = session.scalar(select(TripInvite).where(TripInvite.token_hash == _hash(token)))
    if invite is None or invite.used_at is not None or invite.revoked_at is not None or invite.expires_at <= _now():
        raise _expired()
    if session.scalar(select(TripMember).where(TripMember.trip_id == invite.trip_id, TripMember.user_id == user_id)) is not None:
        raise HTTPException(status_code=409, detail={"code": "ALREADY_A_MEMBER", "message": "你已经加入这段旅行"})
    if session.scalar(select(func.count(TripMember.id)).where(TripMember.trip_id == invite.trip_id)) >= 2:
        raise _full()
    trip = session.get(Trip, invite.trip_id)
    if trip is None:
        raise _not_found()
    _ensure_user(session, user_id)
    session.add(TripMember(trip_id=trip.id, user_id=user_id))
    invite.used_at = _now()
    session.commit()
    return {"data": {"id": trip.id, "name": trip.name, "default_currency": trip.default_currency, "member_count": 2}}