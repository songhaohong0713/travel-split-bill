import logging
from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from app.api.dependencies import CurrentUser, DbSession
from app.api.v1.trips import (
    _cloudbase,
    _cloudbase_error,
    _not_found,
    require_trip_member,
)
from app.core.ledger import (
    FORMAT_VERSION,
    amount,
    annotate_transfers,
    basis_currencies,
    drop_broken_plans,
    find_transfer,
    fix_plans,
    mark_matches,
    member_settlement,
    payment_preview,
    project,
    same_pair,
    settlement_basis,
    settlement_scope,
    snapshot_token,
    trip_today,
)
from app.db.ledger_reads import member_snapshots
from app.db.models import ExpenseRecord, TripLedger, TripMember
from app.db.payment_projection import sync_payments
from app.providers.cloudbase_pg import (
    CloudBasePgConfigurationError,
    CloudBasePgRequestError,
    CloudBasePgUnavailable,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/trips", tags=["ledger"])


@router.get("/ledger-summary")
async def ledger_summary(request: Request, session: DbSession, user_id: CurrentUser):
    cloud = _cloudbase(request)
    if cloud is not None:
        try:
            rows = await cloud.rpc(
                "tsb_member_ledger_snapshots", {"p_user_id": user_id}
            )
        except (
            CloudBasePgConfigurationError,
            CloudBasePgRequestError,
            CloudBasePgUnavailable,
        ) as exc:
            raise _cloudbase_error(exc) from exc
        if not isinstance(rows, list):
            raise _cloudbase_error(ValueError("invalid ledger summaries"))
        snapshots = [
            (row["trip_id"], {k: v for k, v in row.items() if k != "trip_id"})
            for row in rows
        ]
    else:
        snapshots = member_snapshots(session, user_id)
    totals: dict[str, dict[str, Decimal]] = {}
    summaries = []
    for trip_id, old in snapshots:
        try:
            summary = trip_summary(trip_id, old, user_id)
        except Exception:  # One broken trip must not hide the rest.
            logger.exception("ledger summary failed for trip %s", trip_id)
            summary = {**EMPTY_SUMMARY, "id": trip_id}
        for transfer in summary["pending"]:
            if transfer["direction"] == "offset":
                continue
            total = totals.setdefault(
                transfer["currency"],
                {"receivable": Decimal(0), "payable": Decimal(0)},
            )
            total[transfer["direction"]] += Decimal(transfer["amount"])
        summaries.append(summary)
    return {
        "data": {
            "contract_version": 2,
            "trips": summaries,
            "groups": [
                {
                    "currency": currency,
                    **{key: amount(currency, value) for key, value in values.items()},
                }
                for currency, values in sorted(totals.items())
            ],
        }
    }


EMPTY_SUMMARY = {
    "pending": [],
    "incomplete": True,
    "pending_days": 0,
    "day_count": 0,
    "expense_count": 0,
    "latest_expense_date": None,
    "settled_at": None,
    "has_recording": False,
    "history_status": "not_required",
}


def trip_summary(trip_id, old, user_id):
    ledger = project(old)
    closed = [day for day in ledger["days"] if day["status"] != "recording"]
    transfers = []
    if closed and all(day["status"] != "invalid" for day in closed):
        transfers = payment_preview(old, ledger=ledger)["transfers"]
    return {
        "id": trip_id,
        # Transfers between other members are not this user's money. An offset
        # has no payer: it is neither owed to nor by the member it closes.
        "pending": [
            {
                **transfer,
                "direction": "offset"
                if transfer["from_participant_id"] is None
                else "receivable"
                if transfer["to_participant_id"] == user_id
                else "payable",
            }
            for transfer in transfers
            if user_id
            in (transfer["from_participant_id"], transfer["to_participant_id"])
        ],
        "incomplete": any(day["error"] for day in ledger["days"]),
        "pending_days": sum(day["status"] == "pending" for day in closed),
        "day_count": len({r["occurred_at"] for r in old["expenses"]}),
        "expense_count": len(old["expenses"]),
        "latest_expense_date": max(
            (r["occurred_at"] for r in old["expenses"]), default=None
        ),
        "settled_at": max(
            (p["created_at"] for p in ledger["payments"] if not p.get("revoked_at")),
            default=None,
        ),
        "has_recording": any(d["status"] == "recording" for d in ledger["days"]),
        "history_status": ledger["history_status"],
    }


def conflict():
    return HTTPException(
        status_code=409,
        detail={
            "code": "LEDGER_CONFLICT",
            "message": "账单已变化，请刷新金额后重新确认",
        },
    )


class Scope(BaseModel):
    dates: list[date] | None = Field(default=None, max_length=3660)


class PaymentRequest(Scope):
    mark_id: str | None = None
    token: str = Field(min_length=64, max_length=64)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    # Which transfer of the proposal; may be omitted while a trip has two members.
    from_participant_id: str | None = Field(default=None, min_length=1, max_length=36)
    to_participant_id: str | None = Field(default=None, min_length=1, max_length=36)
    # Instead of a pair: the member whose days cancel out (an offset, no money).
    offset_participant_id: str | None = Field(default=None, min_length=1, max_length=36)
    # Tells apart two proposed transfers between the same pair.
    plan_id: str | None = Field(default=None, min_length=1, max_length=36)


async def snapshot(trip_id, request, session, user_id):
    cloud = _cloudbase(request)
    if cloud is not None:
        try:
            result = await cloud.rpc(
                "tsb_ledger_snapshot", {"p_trip_id": trip_id, "p_user_id": user_id}
            )
        except (
            CloudBasePgConfigurationError,
            CloudBasePgRequestError,
            CloudBasePgUnavailable,
        ) as exc:
            raise _cloudbase_error(exc) from exc
        if not isinstance(result, dict) or result.get("not_found"):
            raise _not_found()
        return result
    trip = require_trip_member(session, trip_id, user_id)
    ledger = session.get(TripLedger, trip_id)
    records = session.scalars(
        select(ExpenseRecord)
        .where(ExpenseRecord.trip_id == trip_id)
        .order_by(ExpenseRecord.id)
    ).all()
    members = sorted(
        session.scalars(
            select(TripMember.user_id).where(TripMember.trip_id == trip_id)
        ).all()
    )
    return {
        "currency": trip.default_currency,
        "timezone_offset_minutes": trip.timezone_offset_minutes,
        "members": members,
        "revision": ledger.revision if ledger else 0,
        "state": deepcopy(ledger.state_json) if ledger else {"payments": []},
        "expenses": [
            {
                "id": r.id,
                "revision": r.revision,
                "occurred_at": str(r.occurred_at),
                "payload": r.payload_json,
            }
            for r in records
        ],
    }


async def persist(trip_id, request, session, user_id, old, state):
    cloud = _cloudbase(request)
    if cloud is not None:
        try:
            result = await cloud.rpc(
                "tsb_commit_ledger",
                {
                    "p_trip_id": trip_id,
                    "p_user_id": user_id,
                    "p_expected": old,
                    "p_state": state,
                },
            )
        except (
            CloudBasePgConfigurationError,
            CloudBasePgRequestError,
            CloudBasePgUnavailable,
        ) as exc:
            raise _cloudbase_error(exc) from exc
        if not isinstance(result, dict) or not result.get("ok"):
            raise conflict()
        return
    if old["revision"] == 0:
        from sqlalchemy.exc import IntegrityError

        try:
            session.add(TripLedger(trip_id=trip_id, revision=1, state_json=state))
            sync_payments(session, trip_id, state)
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise conflict() from exc
    else:
        result = session.execute(
            update(TripLedger)
            .where(
                TripLedger.trip_id == trip_id, TripLedger.revision == old["revision"]
            )
            .values(revision=old["revision"] + 1, state_json=state)
        )
        if result.rowcount != 1:
            session.rollback()
            raise conflict()
        sync_payments(session, trip_id, state)
        session.commit()


@router.get("/{trip_id}/ledger")
async def get_ledger(
    trip_id: str, request: Request, session: DbSession, user_id: CurrentUser
):
    old = await snapshot(trip_id, request, session, user_id)
    result = project(old)
    result["contract_version"] = 2
    result["currency"] = old["currency"]
    result["timezone_offset_minutes"] = old.get("timezone_offset_minutes")
    result["business_today"] = trip_today(old)
    annotate_transfers(old, result)
    try:
        result["summary"] = payment_preview(old, ledger=result)["transfers"]
    except ValueError as exc:
        result["summary"] = []
        if any(d["status"] in ("pending", "invalid") for d in result["days"]):
            result["summary_error"] = str(exc)
    result["expense_summaries"] = []
    totals: dict[str, Decimal] = {}
    for record in old["expenses"]:
        day = project({**old, "expenses": [record], "state": {"payments": []}})["days"][
            0
        ]
        result["expense_summaries"].append(
            {"id": record["id"], "groups": day["groups"], "error": day["error"]}
        )
        for group in day["groups"]:
            currency = group["currency"]
            totals[currency] = totals.get(currency, Decimal(0)) + sum(
                (Decimal(v["amount"]) for v in group["paid_by_participant"].values()),
                Decimal(0),
            )
    result["totals"] = [
        {"currency": c, "amount": amount(c, value)}
        for c, value in sorted(totals.items())
    ]
    result["summary_incomplete"] = any(d["status"] == "invalid" for d in result["days"])
    return {"data": result}


@router.post("/{trip_id}/payments/preview")
async def preview_payment(
    trip_id: str,
    body: Scope,
    request: Request,
    session: DbSession,
    user_id: CurrentUser,
):
    old = await snapshot(trip_id, request, session, user_id)
    try:
        return {
            "data": payment_preview(
                old,
                [d.isoformat() for d in body.dates] if body.dates is not None else None,
            )
        }
    except ValueError as exc:
        raise HTTPException(
            422, detail={"code": "INVALID_SETTLEMENT_SCOPE", "message": str(exc)}
        ) from exc


def requested_transfer(old, body):
    """The proposal for the requested range and the one transfer being acted on."""
    if snapshot_token(old) != body.token:
        raise conflict()
    pair = (body.from_participant_id, body.to_participant_id)
    offset = body.offset_participant_id
    if (
        (pair[0] is None) != (pair[1] is None)
        or (offset is not None and pair[0] is not None)
        or (offset is None and pair[0] is None and len(old["members"]) > 2)
    ):
        raise HTTPException(
            422,
            detail={
                "code": "TRANSFER_PAIR_REQUIRED",
                "message": "请指明这笔转账的付款方和收款方",
            },
        )
    try:
        scope = settlement_scope(
            old, [d.isoformat() for d in body.dates] if body.dates is not None else None
        )
    except ValueError as exc:
        raise HTTPException(
            422, detail={"code": "INVALID_SETTLEMENT_SCOPE", "message": str(exc)}
        ) from exc
    blocked = [c for c in scope.get("conflicts", []) if c["currency"] == body.currency]
    if blocked:
        dates = sorted({day for c in blocked for day in c["dates"]})
        raise HTTPException(
            409,
            detail={
                "code": "MARKED_TRANSFER_PENDING",
                "message": "这些日期已有人标记付款或确认收款，请先处理："
                + "、".join(dates),
                "dates": dates,
                # Member ids; the client shows their names.
                "marked_by": sorted({u for c in blocked for u in c["marked_by"]}),
                "confirmed_by": sorted({u for c in blocked for u in c["confirmed_by"]}),
            },
        )
    mark = next(
        (m for m in old["state"].get("payer_marks", []) if m["id"] == body.mark_id),
        None,
    )
    if mark is not None and body.plan_id is None:
        # The mark names its own transfer, also among several of one pair.
        found = next((t for t in scope["transfers"] if mark_matches(mark, t)), None)
    else:
        found = find_transfer(
            scope, body.currency, *pair, offset=offset, plan=body.plan_id
        )
    return scope, found, mark


@router.post("/{trip_id}/payments", status_code=201)
async def mark_payment(
    trip_id: str,
    body: PaymentRequest,
    request: Request,
    session: DbSession,
    user_id: CurrentUser,
):
    old = await snapshot(trip_id, request, session, user_id)
    p, group, mark = requested_transfer(old, body)
    if group is None:
        if body.mark_id:
            raise conflict()
        raise HTTPException(
            422, detail={"code": "NOTHING_TO_PAY", "message": "该币种没有待结清金额"}
        )
    if not group.get("offset_only") and group["to_participant_id"] != user_id:
        raise HTTPException(
            403,
            detail={"code": "RECIPIENT_REQUIRED", "message": "仅收款方可以确认已收款"},
        )
    if body.mark_id and (
        mark is None
        or mark.get("revoked_at")
        or mark.get("confirmed_payment_id")
        or not mark_matches(mark, group)
    ):
        raise conflict()
    state = deepcopy(old["state"])
    payment = {
        **group,
        "id": str(uuid4()),
        "created_at": datetime.now(UTC).isoformat(),
        "created_by": user_id,
        "snapshot_token": body.token,
    }
    if len(old["members"]) > 2:
        drop_broken_plans(old, state)
        fix_plans(state, p, user_id)
        # Fixing the plans has just given the row its plan id.
        payment.update(
            format_version=FORMAT_VERSION,
            plan_id=group["plan_id"],
            **member_settlement(old, group),
        )
    else:
        basis = settlement_basis(
            old,
            {**p, "dates": [a["date"] for a in group["allocations"]]},
            body.currency,
        )
        basis_dates = {b["date"] for b in basis}
        payment.update(
            basis_currencies=basis_currencies(old, basis),
            freeze_basis=basis,
            depends_on=[
                payment["id"]
                for payment in old["state"]["payments"]
                if not payment.get("revoked_at")
                and payment["currency"] == body.currency
                and any(
                    a["date"] in basis_dates
                    for a in payment["allocations"] + payment.get("freeze_basis", [])
                )
            ],
        )
    if mark is not None:
        payment["mark_id"] = mark["id"]
        for saved_mark in state.get("payer_marks", []):
            if saved_mark["id"] == mark["id"]:
                saved_mark["confirmed_payment_id"] = payment["id"]
                saved_mark["confirmed_at"] = payment["created_at"]
    state["payments"].append(payment)
    await persist(trip_id, request, session, user_id, old, state)
    return {"data": payment}


@router.post("/{trip_id}/payments/{payment_id}/revoke")
async def revoke_payment(
    trip_id: str,
    payment_id: str,
    request: Request,
    session: DbSession,
    user_id: CurrentUser,
):
    old = await snapshot(trip_id, request, session, user_id)
    state = deepcopy(old["state"])
    payment = next((p for p in state["payments"] if p["id"] == payment_id), None)
    if payment is None:
        raise _not_found()
    if not payment.get("revoked_at"):
        dependents = [
            p
            for p in state["payments"]
            if not p.get("revoked_at") and payment_id in p.get("depends_on", [])
        ]
        if dependents:
            dates = sorted({a["date"] for p in dependents for a in p["allocations"]})
            raise HTTPException(
                409,
                detail={
                    "code": "PAYMENT_DEPENDENCY",
                    "message": "请先撤销以下日期的后续结清：" + "、".join(dates),
                    "dates": dates,
                },
            )
        payment.update(revoked_at=datetime.now(UTC).isoformat(), revoked_by=user_id)
        await persist(trip_id, request, session, user_id, old, state)
    return {"data": payment}


@router.post("/{trip_id}/payments/mark-paid", status_code=201)
async def mark_paid(
    trip_id: str,
    body: PaymentRequest,
    request: Request,
    session: DbSession,
    user_id: CurrentUser,
):
    old = await snapshot(trip_id, request, session, user_id)
    p, group, _ = requested_transfer(old, body)
    if group is None or group.get("offset_only"):
        raise HTTPException(
            422, detail={"code": "NOTHING_TO_PAY", "message": "没有待付款金额"}
        )
    if group["from_participant_id"] != user_id:
        raise HTTPException(
            403, detail={"code": "PAYER_REQUIRED", "message": "仅付款方可以标记已付款"}
        )
    state = deepcopy(old["state"])
    marks = state.setdefault("payer_marks", [])
    for previous in marks:
        # Other pairs' marks in the same range are untouched.
        if (
            not previous.get("revoked_at")
            and not previous.get("confirmed_payment_id")
            and same_pair(previous, group)
            and set(previous["dates"]) & set(p["dates"])
        ):
            previous.update(
                revoked_at=datetime.now(UTC).isoformat(),
                revoked_by=user_id,
                revoke_reason="superseded",
            )
    mark = {
        **group,
        "id": str(uuid4()),
        "dates": p["dates"],
        "token": body.token,
        "marked_by": user_id,
        "marked_at": datetime.now(UTC).isoformat(),
    }
    if len(old["members"]) > 2:
        # The first mark in a range fixes its whole proposal.
        drop_broken_plans(old, state)
        fix_plans(state, p, user_id)
        mark["plan_id"] = group["plan_id"]
    marks.append(mark)
    await persist(trip_id, request, session, user_id, old, state)
    return {"data": mark}


@router.post("/{trip_id}/payer-marks/{mark_id}/revoke")
async def revoke_mark(
    trip_id: str,
    mark_id: str,
    request: Request,
    session: DbSession,
    user_id: CurrentUser,
):
    old = await snapshot(trip_id, request, session, user_id)
    state = deepcopy(old["state"])
    mark = next((m for m in state.get("payer_marks", []) if m["id"] == mark_id), None)
    if mark is None:
        raise _not_found()
    if mark["marked_by"] != user_id:
        raise HTTPException(
            403,
            detail={"code": "PAYER_REQUIRED", "message": "仅付款方可以撤销付款标记"},
        )
    if mark.get("confirmed_payment_id"):
        raise conflict()
    if not mark.get("revoked_at"):
        mark.update(revoked_at=datetime.now(UTC).isoformat(), revoked_by=user_id)
        await persist(trip_id, request, session, user_id, old, state)
    return {"data": mark}
