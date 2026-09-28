from datetime import date
from typing import Annotated
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.dependencies import CurrentUser, DbSession
from app.api.v1.trips import _not_found
from app.core.allocation import Allocation
from app.core.money import Money
from app.core.settlement import (
    Adjustment,
    Expense,
    LineItem,
    calculate_grouped_settlement,
)
from app.db.models import SettlementVersion, Trip
from app.providers.cloudbase_pg import (
    CloudBasePgClient,
    CloudBasePgConfigurationError,
    CloudBasePgUnavailable,
)
from app.providers.rates import FrankfurterRates, RateUnavailable

router = APIRouter(prefix="/v1", tags=["settlements"])


class MoneyInput(BaseModel):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    amount: str = Field(pattern=r"^-?\d+(\.\d+)?$")

    def to_money(self) -> Money:
        return Money(currency=self.currency, amount=self.amount)


class ItemInput(BaseModel):
    item_id: str
    amount: MoneyInput
    allocation: dict[str, str]
    tax_amount: MoneyInput | None = None
    tax_included: bool = False

    def to_line_item(self) -> LineItem:
        return LineItem(
            item_id=self.item_id,
            amount=self.amount.to_money(),
            allocation=Allocation.from_shares(self.allocation),
            tax_amount=self.tax_amount.to_money() if self.tax_amount else None,
            tax_included=self.tax_included,
        )


class AdjustmentInput(BaseModel):
    adjustment_id: str
    amount: MoneyInput
    allocation: dict[str, str]
    reason: str
    received_by: str | None = None

    def to_adjustment(self) -> Adjustment:
        return Adjustment(
            adjustment_id=self.adjustment_id,
            amount=self.amount.to_money(),
            allocation=Allocation.from_shares(self.allocation),
            reason=self.reason,
            received_by=self.received_by,
        )


class ExpenseInput(BaseModel):
    expense_id: str
    payer_id: str
    items: list[ItemInput]
    adjustments: list[AdjustmentInput] = []
    actual_payment: MoneyInput | None = None
    reference_rate: str | None = None
    reference_rate_source: str | None = None
    payment_to_settlement_rate: str | None = None
    payment_to_settlement_rate_source: str | None = None
    rounding_owner_id: str | None = None
    settlement_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")

    def to_expense(self, default_settlement_currency: str) -> Expense:
        return Expense(
            expense_id=self.expense_id,
            payer_id=self.payer_id,
            items=tuple(item.to_line_item() for item in self.items),
            adjustments=tuple(
                adjustment.to_adjustment() for adjustment in self.adjustments
            ),
            actual_payment=self.actual_payment.to_money()
            if self.actual_payment
            else None,
            reference_rate=self.reference_rate,
            reference_rate_source=self.reference_rate_source,
            payment_to_settlement_rate=self.payment_to_settlement_rate,
            payment_to_settlement_rate_source=self.payment_to_settlement_rate_source,
            rounding_owner_id=self.rounding_owner_id,
            settlement_currency=self.settlement_currency or default_settlement_currency,
        )


class PreviewRequest(BaseModel):
    participants: list[str]
    settlement_currency: str = Field(pattern=r"^[A-Z]{3}$")
    expenses: list[ExpenseInput]


def _money(money: Money) -> dict[str, str]:
    return {"currency": money.currency, "amount": money.amount}


def _result_payload(result) -> dict[str, object]:
    return {
        "responsibility_by_participant": {key: _money(value) for key, value in result.responsibility_by_participant.items()},
        "paid_by_participant": {key: _money(value) for key, value in result.paid_by_participant.items()},
        "net_by_participant": {key: _money(value) for key, value in result.net_by_participant.items()},
        "transfers": [{"from_participant_id": transfer.from_participant_id, "to_participant_id": transfer.to_participant_id, "amount": _money(transfer.amount)} for transfer in result.transfers],
        "audit_lines": [{"expense_id": line.expense_id, "participant_id": line.participant_id, "amount": _money(line.amount), "reason": line.reason, "rate_source": line.rate_source} for line in result.audit_lines],
    }


def _grouped_payload(participants: tuple[str, ...], expenses: tuple[Expense, ...]) -> dict[str, object]:
    groups = calculate_grouped_settlement(participants, expenses)
    result: dict[str, object] = {
        "groups": [
            {"currency": currency, **_result_payload(group)}
            for currency, group in groups.items()
        ]
    }
    if len(groups) == 1:
        result.update(_result_payload(next(iter(groups.values()))))
    return result

def _cloudbase(request: Request) -> CloudBasePgClient | None:
    return getattr(request.app.state, "cloudbase_pg", None)

def _cloudbase_error(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"code": "DATABASE_UNAVAILABLE", "message": "CloudBase PostgreSQL is unavailable"},
    )


@router.get("/exchange-rates")
async def exchange_rate(
    _: CurrentUser,
    requested_date: Annotated[date, Query(alias="date")],
    from_currency: Annotated[str, Query(pattern=r"^[A-Z]{3}$")],
    to_currency: Annotated[str, Query(pattern=r"^[A-Z]{3}$")],
) -> dict[str, object]:
    try:
        async with httpx.AsyncClient() as client:
            quote = await FrankfurterRates(client).quote(
                requested_date, from_currency, to_currency
            )
    except RateUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "RATE_UNAVAILABLE", "message": str(exc)},
        ) from exc
    return {"data": quote}



@router.post("/trips/{trip_id}/settlements/preview")
async def preview_settlement(
    trip_id: str, body: PreviewRequest, http_request: Request, session: DbSession, user_id: CurrentUser
) -> dict[str, object]:
    cloudbase = _cloudbase(http_request)
    if cloudbase is not None:
        try:
            rows = await cloudbase.request("GET", "/trip_members", params={"trip_id": f"eq.{trip_id}", "user_id": f"eq.{user_id}", "select": "id", "limit": "1"})
        except (CloudBasePgConfigurationError, CloudBasePgUnavailable) as exc:
            raise _cloudbase_error(exc) from exc
        if not isinstance(rows, list) or not rows:
            raise _not_found()
    else:
        trip = session.scalar(select(Trip).where(Trip.id == trip_id, Trip.owner_id == user_id))
        if trip is None:
            raise _not_found()
    expenses = tuple(expense.to_expense(body.settlement_currency) for expense in body.expenses)
    return {"data": _grouped_payload(tuple(body.participants), expenses)}

@router.post("/trips/{trip_id}/settlements/publish", status_code=status.HTTP_201_CREATED)
async def publish_settlement(
    trip_id: str, body: PreviewRequest, http_request: Request, session: DbSession, user_id: CurrentUser
) -> dict[str, object]:
    expenses = tuple(expense.to_expense(body.settlement_currency) for expense in body.expenses)
    projection = _grouped_payload(tuple(body.participants), expenses)
    cloudbase = _cloudbase(http_request)
    if cloudbase is not None:
        version_id = str(uuid4())
        try:
            response = await cloudbase.rpc("tsb_publish_settlement_version", {"p_version_id": version_id, "p_trip_id": trip_id, "p_owner_id": user_id, "p_result": projection, "p_public": projection})
        except (CloudBasePgConfigurationError, CloudBasePgUnavailable) as exc:
            raise _cloudbase_error(exc) from exc
        if not isinstance(response, dict) or response.get("not_found"):
            raise _not_found()
        return {"data": {"id": str(response["id"]), "trip_id": trip_id, "result": projection}}
    trip = session.scalar(select(Trip).where(Trip.id == trip_id, Trip.owner_id == user_id))
    if trip is None:
        raise _not_found()
    version = SettlementVersion(trip_id=trip_id, result_json=projection, public_json=projection)
    session.add(version)
    session.flush()
    trip.latest_version_id = version.id
    session.commit()
    return {"data": {"id": version.id, "trip_id": trip_id, "result": projection}}
