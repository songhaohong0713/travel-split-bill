from copy import deepcopy
from datetime import UTC, datetime
from datetime import date as CalendarDate
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.dependencies import CurrentUser, DbSession
from app.api.v1.ledger import conflict, persist, snapshot
from app.api.v1.trips import (
    _cloudbase,
    _cloudbase_error,
    _not_found,
    require_trip_owner,
)
from app.core.history import historical_plan
from app.providers.cloudbase_pg import (
    CloudBasePgConfigurationError,
    CloudBasePgRequestError,
    CloudBasePgUnavailable,
)

router = APIRouter(prefix="/v1/trips", tags=["historical-confirmation"])


class HistoricalPayment(BaseModel):
    date: CalendarDate
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    amount: str = Field(pattern=r"^\d+(\.\d+)?$", max_length=24)
    from_participant_id: str = Field(min_length=1, max_length=36)
    to_participant_id: str = Field(min_length=1, max_length=36)
    paid_on: CalendarDate | None = None


class HistorySelection(BaseModel):
    mode: Literal["unpaid", "settled", "partial"]
    payments: list[HistoricalPayment] = Field(default_factory=list, max_length=1000)
    paid_on: CalendarDate | None = None


class HistoryConfirmation(HistorySelection):
    token: str = Field(min_length=64, max_length=64)


async def require_owner(trip_id, request, session, user_id):
    cloud = _cloudbase(request)
    if cloud is None:
        require_trip_owner(session, trip_id, user_id)
        return
    try:
        rows = await cloud.request(
            "GET",
            "/trips",
            params={
                "id": f"eq.{trip_id}",
                "owner_id": f"eq.{user_id}",
                "select": "id",
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
        raise _not_found()


@router.post("/{trip_id}/history/prepare")
async def prepare_history(
    trip_id: str, request: Request, session: DbSession, user_id: CurrentUser
):
    await require_owner(trip_id, request, session, user_id)
    old = await snapshot(trip_id, request, session, user_id)
    if not old["expenses"]:
        raise HTTPException(
            422, detail={"code": "EMPTY_HISTORY", "message": "没有历史消费需要确认"}
        )
    state = deepcopy(old["state"])
    status = state.get("history_confirmation", {}).get("status")
    if status == "pending":
        return {"data": {"status": "pending"}}
    if status == "confirmed" or state["payments"] or state["closed_dates"]:
        raise HTTPException(
            409,
            detail={
                "code": "HISTORY_ALREADY_ACTIVE",
                "message": "已有结清操作，不能重新初始化历史",
            },
        )
    state["history_confirmation"] = {
        "status": "pending",
        "prepared_by": user_id,
        "prepared_at": datetime.now(UTC).isoformat(),
    }
    await persist(trip_id, request, session, user_id, old, state)
    return {"data": {"status": "pending"}}


def plan(old, body, user_id):
    selection = body.model_dump(mode="json", exclude={"token"})
    try:
        result = historical_plan(old, selection, user_id)
        if any(not p.get("offset_only") and p["to_participant_id"] != user_id for p in result["payments"]):
            raise HTTPException(403, detail={"code": "RECIPIENT_REQUIRED", "message": "仅收款方可以确认历史已收款；付款方请使用标记付款流程"})
        return result
    except (ValueError, ArithmeticError) as exc:
        raise HTTPException(
            422, detail={"code": "INVALID_HISTORY_CONFIRMATION", "message": str(exc)}
        ) from exc


@router.post("/{trip_id}/history/preview")
async def preview_history(
    trip_id: str,
    body: HistorySelection,
    request: Request,
    session: DbSession,
    user_id: CurrentUser,
):
    if body.mode == "unpaid":
        await require_owner(trip_id, request, session, user_id)
    proposed = plan(await snapshot(trip_id, request, session, user_id), body, user_id)
    return {"data": {key: value for key, value in proposed.items() if key != "state"}}


@router.post("/{trip_id}/history/confirm")
async def confirm_history(
    trip_id: str,
    body: HistoryConfirmation,
    request: Request,
    session: DbSession,
    user_id: CurrentUser,
):
    if body.mode == "unpaid":
        await require_owner(trip_id, request, session, user_id)
    old = await snapshot(trip_id, request, session, user_id)
    if old["state"].get("history_confirmation", {}).get("status") != "pending":
        raise conflict()
    proposed = plan(old, body, user_id)
    if proposed["token"] != body.token:
        raise conflict()
    await persist(trip_id, request, session, user_id, old, proposed["state"])
    return {"data": {"status": "confirmed", "remaining": proposed["remaining"]}}
