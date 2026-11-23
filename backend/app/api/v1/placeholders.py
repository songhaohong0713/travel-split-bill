"""Members who do not use the app: adding, deleting and claiming them."""

import secrets
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import delete, or_, select

from app.api.dependencies import CurrentUser, DbSession
from app.api.v1.ledger import placeholder_members, snapshot
from app.api.v1.profile import NicknameRequest
from app.api.v1.trip_invites import (
    _expired,
    _full,
    _hash,
    _lock_trip,
    _member_count,
    _now,
)
from app.api.v1.trips import (
    _cloudbase,
    _cloudbase_error,
    _ensure_user,
    _not_found,
)
from app.core.limits import MAX_TRIP_MEMBERS
from app.core.placeholders import ClaimRefused, claim, removal
from app.db.ledger_reads import trip_snapshot
from app.db.models import (
    ExpenseRecord,
    PaymentAllocationRecord,
    PaymentRecord,
    TripInvite,
    TripLedger,
    TripMember,
    User,
)
from app.providers.cloudbase_pg import (
    CloudBasePgConfigurationError,
    CloudBasePgRequestError,
    CloudBasePgUnavailable,
)

router = APIRouter(prefix="/v1", tags=["placeholder-members"])
CLOUD_ERRORS = (
    CloudBasePgConfigurationError,
    CloudBasePgRequestError,
    CloudBasePgUnavailable,
)


def _in_use() -> HTTPException:
    return HTTPException(
        409,
        detail={
            "code": "PLACEHOLDER_IN_USE",
            "message": "这位成员已有消费或结清记录，不能删除",
        },
    )


def _unavailable() -> HTTPException:
    return HTTPException(
        409,
        detail={
            "code": "PLACEHOLDER_UNAVAILABLE",
            "message": "这位成员已被认领或删除，请重新选择",
        },
    )


def _changed() -> HTTPException:
    return HTTPException(
        409,
        detail={"code": "TRIP_CHANGED", "message": "旅行数据刚有变化，请重试"},
    )


def _already_member() -> HTTPException:
    return HTTPException(
        409, detail={"code": "ALREADY_A_MEMBER", "message": "你已经加入这段旅行"}
    )


def _refused(exc: ClaimRefused) -> HTTPException:
    return HTTPException(409, detail={"code": "CLAIM_REFUSED", "message": str(exc)})


async def _rpc(cloud, name: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        result = await cloud.rpc(name, payload)
    except CLOUD_ERRORS as exc:
        raise _cloudbase_error(exc) from exc
    if not isinstance(result, dict):
        raise _cloudbase_error(ValueError("invalid CloudBase response"))
    return result


@router.post("/trips/{trip_id}/placeholders", status_code=status.HTTP_201_CREATED)
async def add_placeholder(
    trip_id: str,
    body: NicknameRequest,
    request: Request,
    session: DbSession,
    user_id: CurrentUser,
):
    """Any member adds a person by name; they count toward the member limit."""
    placeholder_id = secrets.token_urlsafe(18)
    cloud = _cloudbase(request)
    if cloud is not None:
        result = await _rpc(
            cloud,
            "tsb_add_placeholder_member",
            {
                "p_member_id": secrets.token_urlsafe(18),
                "p_placeholder_id": placeholder_id,
                "p_trip_id": trip_id,
                "p_user_id": user_id,
                "p_nickname": body.nickname,
            },
        )
        if result.get("not_found"):
            raise _not_found()
        if result.get("full"):
            raise _full()
    else:
        _lock_trip(session, trip_id, None)
        if not _is_member(session, trip_id, user_id):
            raise _not_found()
        if _member_count(session, trip_id) >= MAX_TRIP_MEMBERS:
            raise _full()
        session.add(User(id=placeholder_id, wechat_openid_hash=None))
        session.flush()
        session.add(
            TripMember(
                trip_id=trip_id,
                user_id=placeholder_id,
                nickname=body.nickname,
                is_placeholder=True,
                added_by=user_id,
            )
        )
        session.commit()
    return {
        "data": {
            "id": placeholder_id,
            "nickname": body.nickname,
            "is_placeholder": True,
        }
    }


def _is_member(session, trip_id, user_id) -> bool:
    return (
        session.scalar(
            select(TripMember.id).where(
                TripMember.trip_id == trip_id, TripMember.user_id == user_id
            )
        )
        is not None
    )


def _placeholder(session, trip_id, placeholder_id) -> TripMember | None:
    return session.scalar(
        select(TripMember).where(
            TripMember.trip_id == trip_id,
            TripMember.user_id == placeholder_id,
            TripMember.is_placeholder,
        )
    )


def _write_expenses(session, trip_id, expenses) -> None:
    """Store rewritten payloads; an editor holding the old one must reload."""
    for change in expenses:
        record = session.get(ExpenseRecord, change["id"])
        if record is None or record.trip_id != trip_id:
            raise _changed()
        record.payload_json = change["payload"]
        record.revision += 1


def _delete_placeholder_user(session, placeholder_id) -> None:
    """Remove the user row once nothing refers to it any more.

    PostgreSQL would refuse through its foreign keys; SQLite does not enforce
    them, so the same guarantee is checked here.
    """
    session.flush()
    referenced = session.scalar(
        select(TripMember.id).where(TripMember.user_id == placeholder_id)
    ) or session.scalar(
        select(PaymentRecord.id).where(
            or_(
                PaymentRecord.from_id == placeholder_id,
                PaymentRecord.to_id == placeholder_id,
            )
        )
    )
    if referenced is not None:
        raise RuntimeError("placeholder user is still referenced")
    session.execute(delete(User).where(User.id == placeholder_id))


@router.delete(
    "/trips/{trip_id}/placeholders/{placeholder_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_placeholder(
    trip_id: str,
    placeholder_id: str,
    request: Request,
    session: DbSession,
    user_id: CurrentUser,
):
    """Any member removes a placeholder that never paid, owed or settled."""
    cloud = _cloudbase(request)
    if cloud is not None:
        old = await snapshot(trip_id, request, session, user_id)
        if placeholder_id not in await placeholder_members(trip_id, request, session):
            raise _not_found()
        plan = removal(old, placeholder_id)
        if plan is None:
            raise _in_use()
        result = await _rpc(
            cloud,
            "tsb_delete_placeholder_member",
            {
                "p_trip_id": trip_id,
                "p_user_id": user_id,
                "p_placeholder_id": placeholder_id,
                "p_expected": old,
                "p_expenses": plan.get("expenses", []),
            },
        )
        if result.get("not_found"):
            raise _not_found()
        if result.get("conflict"):
            raise _changed()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    trip = _lock_trip(session, trip_id, None)
    if not _is_member(session, trip_id, user_id):
        raise _not_found()
    member = _placeholder(session, trip_id, placeholder_id)
    if member is None:
        raise _not_found()
    plan = removal(trip_snapshot(session, trip), placeholder_id)
    if plan is None:
        session.rollback()
        raise _in_use()
    try:
        _write_expenses(session, trip_id, plan["expenses"])
        session.delete(member)
        _delete_placeholder_user(session, placeholder_id)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class ClaimRequest(BaseModel):
    placeholder_id: str


@router.post("/trip-invites/{token}/claim")
async def claim_placeholder(
    token: str,
    body: ClaimRequest,
    request: Request,
    session: DbSession,
    user_id: CurrentUser,
):
    """Join through an invite link as one of the trip's placeholders.

    The placeholder's id becomes the caller's id in the member row, in every
    expense, in the ledger state and in the settlement records, in a single
    transaction; then the placeholder's user row is deleted.
    """
    cloud = _cloudbase(request)
    if cloud is not None:
        return {"data": await _claim_cloud(cloud, token, body.placeholder_id, user_id)}
    located = session.execute(
        select(TripInvite.id, TripInvite.trip_id).where(
            TripInvite.token_hash == _hash(token)
        )
    ).one_or_none()
    if located is None:
        raise _expired()
    invite_id, trip_id = located
    # End the lookup before locking, as joining does.
    session.commit()
    trip = _lock_trip(session, trip_id, None)
    invite = session.scalar(
        select(TripInvite).where(TripInvite.id == invite_id).with_for_update()
    )
    if (
        invite is None
        or invite.used_at is not None
        or invite.revoked_at is not None
        or invite.expires_at <= _now()
    ):
        raise _expired()
    if _is_member(session, trip_id, user_id):
        raise _already_member()
    member = _placeholder(session, trip_id, body.placeholder_id)
    if member is None:
        session.rollback()
        raise _unavailable()
    try:
        rewrite = claim(trip_snapshot(session, trip), body.placeholder_id, user_id)
    except ClaimRefused as exc:
        session.rollback()
        raise _refused(exc) from exc
    try:
        _ensure_user(session, user_id)
        apply_claim(session, trip_id, member, user_id, rewrite)
        _delete_placeholder_user(session, body.placeholder_id)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return {
        "data": {
            "id": trip.id,
            "name": trip.name,
            "default_currency": trip.default_currency,
            "member_count": _member_count(session, trip.id),
        }
    }


def apply_claim(session, trip_id, member, user_id, rewrite) -> None:
    """Write a claim on the local path.

    The settlement rows and the ledger state are replaced together with the
    same documents. The projection that guards settlement history is not
    called and not loosened: its check stays for every other write.
    """
    member.user_id = user_id
    member.is_placeholder = False
    _write_expenses(session, trip_id, rewrite["expenses"])
    for change in rewrite["payments"]:
        row = session.get(PaymentRecord, change["id"])
        if row is None or row.trip_id != trip_id:
            raise _changed()
        row.from_id, row.to_id = change["from_id"], change["to_id"]
        row.payload_json = change["payload"]
        amounts = {a["date"]: Decimal(a["amount"]) for a in change["allocations"]}
        for allocation in session.scalars(
            select(PaymentAllocationRecord).where(
                PaymentAllocationRecord.payment_id == row.id
            )
        ).all():
            allocation.amount = amounts[str(allocation.occurred_at)]
    ledger = session.get(TripLedger, trip_id)
    if ledger is not None:
        # A snapshot read before the claim must not be written back.
        ledger.state_json = rewrite["state"]
        ledger.revision += 1
    elif rewrite["state_changed"]:
        # No settlement yet, but the open proposal was fixed for the claim.
        session.add(
            TripLedger(trip_id=trip_id, revision=1, state_json=rewrite["state"])
        )


async def _claim_cloud(cloud, token, placeholder_id, user_id) -> dict[str, Any]:
    arguments = {
        "p_token_hash": _hash(token),
        "p_user_id": user_id,
        "p_placeholder_id": placeholder_id,
    }
    prepared = await _rpc(cloud, "tsb_claim_snapshot", arguments)
    _raise_claim_flags(prepared)
    old = prepared["snapshot"]
    try:
        rewrite = claim(old, placeholder_id, user_id)
    except ClaimRefused as exc:
        raise _refused(exc) from exc
    result = await _rpc(
        cloud,
        "tsb_claim_placeholder_member",
        {
            **arguments,
            "p_expected": old,
            "p_expenses": rewrite["expenses"],
            # Null leaves a trip that has no ledger row without one.
            "p_state": rewrite["state"]
            if old["revision"] or rewrite["state_changed"]
            else None,
            "p_payments": rewrite["payments"],
        },
    )
    _raise_claim_flags(result)
    return result


def _raise_claim_flags(result) -> None:
    if result.get("expired"):
        raise _expired()
    if result.get("already_member"):
        raise _already_member()
    if result.get("unavailable"):
        raise _unavailable()
    if result.get("conflict"):
        raise _changed()
