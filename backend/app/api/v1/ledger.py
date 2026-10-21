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
from app.core.ledger import amount, payment_preview, project, snapshot_token
from app.db.models import ExpenseRecord, TripLedger, TripMember
from app.providers.cloudbase_pg import (
    CloudBasePgConfigurationError,
    CloudBasePgRequestError,
    CloudBasePgUnavailable,
)

router = APIRouter(prefix="/v1/trips", tags=["ledger"])


@router.get("/ledger-summary")
async def ledger_summary(request: Request, session: DbSession, user_id: CurrentUser):
    from app.api.v1.trips import list_trips

    trips = (await list_trips(request, session, user_id))["data"]
    totals: dict[str, dict[str, Decimal]] = {}
    summaries = []
    for trip in trips:
        old = await snapshot(trip["id"], request, session, user_id)
        ledger = project(old)
        closed = [day for day in ledger["days"] if day["status"] != "recording"]
        pending = []
        if closed and all(day["status"] != "invalid" for day in closed):
            pending = payment_preview(old)["groups"]
        pending = [
            {
                **group,
                "direction": "receivable"
                if group["to_participant_id"] == user_id
                else "payable",
            }
            for group in pending
        ]
        for group in pending:
            currency = group["currency"]
            total = totals.setdefault(
                currency, {"receivable": Decimal(0), "payable": Decimal(0)}
            )
            key = "receivable" if group["to_participant_id"] == user_id else "payable"
            total[key] += Decimal(group["amount"])
        summaries.append(
            {
                "id": trip["id"],
                "pending": pending,
                "incomplete": any(day["error"] for day in ledger["days"]),
                "pending_days": sum(day["status"] == "pending" for day in closed),
            }
        )
    return {
        "data": {
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
    token: str = Field(min_length=64, max_length=64)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


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
        "members": members,
        "revision": ledger.revision if ledger else 0,
        "state": deepcopy(ledger.state_json)
        if ledger
        else {"closed_dates": [], "payments": []},
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
        session.commit()


@router.get("/{trip_id}/ledger")
async def get_ledger(
    trip_id: str, request: Request, session: DbSession, user_id: CurrentUser
):
    old = await snapshot(trip_id, request, session, user_id)
    result = project(old)
    try:
        result["summary"] = payment_preview(old)["groups"]
    except ValueError:
        result["summary"] = []
    result["summary_incomplete"] = any(d["status"] == "invalid" for d in result["days"])
    return {"data": result}


@router.post("/{trip_id}/days/{day}/close")
async def close_day(
    trip_id: str, day: date, request: Request, session: DbSession, user_id: CurrentUser
):
    old = await snapshot(trip_id, request, session, user_id)
    state = deepcopy(old["state"])
    value = day.isoformat()
    if value not in {r["occurred_at"] for r in old["expenses"]}:
        raise HTTPException(
            422, detail={"code": "EMPTY_DAY", "message": "当天没有消费记录"}
        )
    if value not in state["closed_dates"]:
        state["closed_dates"].append(value)
        state.setdefault("closed_by", {})[value] = {
            "user_id": user_id,
            "at": datetime.now(UTC).isoformat(),
        }
        await persist(trip_id, request, session, user_id, old, state)
    return {"data": {"date": value, "closed": True}}


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


@router.post("/{trip_id}/payments", status_code=201)
async def mark_payment(
    trip_id: str,
    body: PaymentRequest,
    request: Request,
    session: DbSession,
    user_id: CurrentUser,
):
    old = await snapshot(trip_id, request, session, user_id)
    if snapshot_token(old) != body.token:
        raise conflict()
    try:
        p = payment_preview(
            old, [d.isoformat() for d in body.dates] if body.dates is not None else None
        )
    except ValueError as exc:
        raise HTTPException(
            422, detail={"code": "INVALID_SETTLEMENT_SCOPE", "message": str(exc)}
        ) from exc
    group = next((g for g in p["groups"] if g["currency"] == body.currency), None)
    if group is None:
        raise HTTPException(
            422, detail={"code": "NOTHING_TO_PAY", "message": "该币种没有待结清金额"}
        )
    payment = {
        **group,
        "id": str(uuid4()),
        "created_at": datetime.now(UTC).isoformat(),
        "created_by": user_id,
        "snapshot_token": body.token,
        "basis_expenses": [
            record for record in old["expenses"] if record["occurred_at"] in p["dates"]
        ],
    }
    state = deepcopy(old["state"])
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
        payment.update(revoked_at=datetime.now(UTC).isoformat(), revoked_by=user_id)
        await persist(trip_id, request, session, user_id, old, state)
    return {"data": payment}
