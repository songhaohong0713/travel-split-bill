import json
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.dependencies import CurrentUser, DbSession
from app.db.models import ExpenseRecord, IdempotencyRecord, Trip, User

router = APIRouter(prefix="/v1", tags=["trips"])


class CreateTripRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    default_currency: str = Field(pattern=r"^[A-Z]{3}$")


class CreateExpenseRequest(BaseModel):
    occurred_at: date
    payload: dict[str, object]


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "NOT_FOUND", "message": "未找到资源"},
    )


def _ensure_user(session: DbSession, user_id: str) -> None:
    if session.get(User, user_id) is None:
        session.add(User(id=user_id, wechat_openid_hash=None))
        session.flush()


@router.post("/trips", status_code=status.HTTP_201_CREATED)
def create_trip(
    request: CreateTripRequest, session: DbSession, user_id: CurrentUser
) -> dict[str, dict[str, str]]:
    _ensure_user(session, user_id)
    trip = Trip(
        owner_id=user_id, name=request.name, default_currency=request.default_currency
    )
    session.add(trip)
    session.commit()
    return {
        "data": {
            "id": trip.id,
            "name": trip.name,
            "default_currency": trip.default_currency,
        }
    }


@router.get("/trips/{trip_id}/expenses")
def list_expenses(
    trip_id: str, session: DbSession, user_id: CurrentUser
) -> dict[str, list[dict[str, object]]]:
    trip = session.scalar(
        select(Trip).where(Trip.id == trip_id, Trip.owner_id == user_id)
    )
    if trip is None:
        raise _not_found()
    expenses = session.scalars(
        select(ExpenseRecord)
        .where(ExpenseRecord.trip_id == trip_id, ExpenseRecord.owner_id == user_id)
        .order_by(ExpenseRecord.created_at)
    ).all()
    return {
        "data": [
            {
                "id": expense.id,
                "revision": expense.revision,
                "occurred_at": expense.occurred_at.isoformat(),
                "payload": expense.payload_json,
            }
            for expense in expenses
        ]
    }


@router.post("/trips/{trip_id}/expenses", status_code=status.HTTP_201_CREATED)
def create_expense(
    trip_id: str,
    request: CreateExpenseRequest,
    session: DbSession,
    user_id: CurrentUser,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Response:
    if idempotency_key is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "IDEMPOTENCY_KEY_REQUIRED", "message": "缺少幂等键"},
        )
    try:
        UUID(idempotency_key)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "IDEMPOTENCY_KEY_INVALID", "message": "幂等键格式无效"},
        ) from exc
    trip = session.scalar(
        select(Trip).where(Trip.id == trip_id, Trip.owner_id == user_id)
    )
    if trip is None:
        raise _not_found()
    existing = session.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.owner_id == user_id,
            IdempotencyRecord.key == idempotency_key,
        )
    )
    if existing is not None:
        return JSONResponse(
            status_code=existing.status_code, content=json.loads(existing.response_json)
        )
    expense = ExpenseRecord(
        trip_id=trip_id,
        owner_id=user_id,
        occurred_at=request.occurred_at,
        payload_json=request.payload,
    )
    session.add(expense)
    session.flush()
    response = {
        "data": {
            "id": expense.id,
            "revision": expense.revision,
            "occurred_at": expense.occurred_at.isoformat(),
            "payload": expense.payload_json,
        }
    }
    session.add(
        IdempotencyRecord(
            owner_id=user_id,
            key=idempotency_key,
            status_code=status.HTTP_201_CREATED,
            response_json=json.dumps(response),
        )
    )
    session.commit()
    return JSONResponse(status_code=status.HTTP_201_CREATED, content=response)
