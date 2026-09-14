import json
from datetime import date
from typing import Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from app.api.dependencies import CurrentUser, DbSession
from app.db.models import ExpenseRecord, IdempotencyRecord, Trip, User
from app.providers.cloudbase_pg import (
    CloudBasePgClient,
    CloudBasePgConfigurationError,
    CloudBasePgUnavailable,
)

router = APIRouter(prefix="/v1", tags=["trips"])


class CreateTripRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    default_currency: str = Field(pattern=r"^[A-Z]{3}$")


class CreateExpenseRequest(BaseModel):
    occurred_at: date
    payload: dict[str, object]


class UpdateExpenseRequest(BaseModel):
    revision: int = Field(ge=1)
    occurred_at: date
    payload: dict[str, object]


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "NOT_FOUND", "message": "未找到资源"},
    )


def _cloudbase_error(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": "DATABASE_UNAVAILABLE", "message": "CloudBase PostgreSQL is unavailable"},
    )


def _ensure_user(session: DbSession, user_id: str) -> None:
    if session.get(User, user_id) is None:
        session.add(User(id=user_id, wechat_openid_hash=None))
        session.flush()


def _cloudbase(request: Request) -> CloudBasePgClient | None:
    return getattr(request.app.state, "cloudbase_pg", None)


@router.post("/trips", status_code=status.HTTP_201_CREATED)
async def create_trip(
    body: CreateTripRequest, http_request: Request, session: DbSession, user_id: CurrentUser
) -> dict[str, dict[str, str]]:
    cloudbase = _cloudbase(http_request)
    if cloudbase is not None:
        trip_id = str(uuid4())
        try:
            result = await cloudbase.rpc(
                "tsb_create_trip",
                {
                    "p_trip_id": trip_id,
                    "p_owner_id": user_id,
                    "p_name": body.name,
                    "p_default_currency": body.default_currency,
                },
            )
        except (CloudBasePgConfigurationError, CloudBasePgUnavailable) as exc:
            raise _cloudbase_error(exc) from exc
        if not isinstance(result, dict):
            raise _cloudbase_error(ValueError("invalid CloudBase trip response"))
        typed_result = cast(dict[str, Any], result)
        return {
            "data": {
                "id": str(typed_result["id"]),
                "name": str(typed_result["name"]),
                "default_currency": str(typed_result["default_currency"]),
            }
        }

    _ensure_user(session, user_id)
    trip = Trip(owner_id=user_id, name=body.name, default_currency=body.default_currency)
    session.add(trip)
    session.commit()
    return {
        "data": {
            "id": trip.id,
            "name": trip.name,
            "default_currency": trip.default_currency,
        }
    }


@router.get("/trips")
async def list_trips(
    http_request: Request, session: DbSession, user_id: CurrentUser
) -> dict[str, list[dict[str, str]]]:
    cloudbase = _cloudbase(http_request)
    if cloudbase is not None:
        try:
            rows = await cloudbase.request(
                "GET",
                "/trips",
                params={
                    "owner_id": f"eq.{user_id}",
                    "select": "id,name,default_currency",
                    "order": "created_at.asc,id.asc",
                },
            )
        except (CloudBasePgConfigurationError, CloudBasePgUnavailable) as exc:
            raise _cloudbase_error(exc) from exc
        if not isinstance(rows, list):
            raise _cloudbase_error(ValueError("invalid CloudBase trip response"))
        typed_rows = cast(list[dict[str, Any]], rows)
        return {
            "data": [
                {
                    "id": str(row["id"]),
                    "name": str(row["name"]),
                    "default_currency": str(row["default_currency"]),
                }
                for row in typed_rows
            ]
        }

    trips = session.scalars(
        select(Trip).where(Trip.owner_id == user_id).order_by(Trip.created_at, Trip.id)
    ).all()
    return {
        "data": [
            {
                "id": trip.id,
                "name": trip.name,
                "default_currency": trip.default_currency,
            }
            for trip in trips
        ]
    }


@router.get("/trips/{trip_id}/expenses")
async def list_expenses(
    trip_id: str, http_request: Request, session: DbSession, user_id: CurrentUser
) -> dict[str, list[dict[str, object]]]:
    cloudbase = _cloudbase(http_request)
    if cloudbase is not None:
        try:
            rows = await cloudbase.request(
                "GET",
                "/expenses",
                params={
                    "trip_id": f"eq.{trip_id}",
                    "owner_id": f"eq.{user_id}",
                    "select": "id,revision,occurred_at,payload_json",
                    "order": "created_at.asc",
                },
            )
        except (CloudBasePgConfigurationError, CloudBasePgUnavailable) as exc:
            raise _cloudbase_error(exc) from exc
        if not isinstance(rows, list):
            raise _cloudbase_error(ValueError("invalid CloudBase expense response"))
        typed_rows = cast(list[dict[str, Any]], rows)
        return {
            "data": [
                {
                    "id": str(row["id"]),
                    "revision": int(row["revision"]),
                    "occurred_at": str(row["occurred_at"]),
                    "payload": row["payload_json"],
                }
                for row in typed_rows
            ]
        }

    trip = session.scalar(select(Trip).where(Trip.id == trip_id, Trip.owner_id == user_id))
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
async def create_expense(
    trip_id: str,
    body: CreateExpenseRequest,
    http_request: Request,
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

    cloudbase = _cloudbase(http_request)
    if cloudbase is not None:
        expense_id = str(uuid4())
        response = {
            "data": {
                "id": expense_id,
                "revision": 1,
                "occurred_at": body.occurred_at.isoformat(),
                "payload": body.payload,
            }
        }
        try:
            result = await cloudbase.rpc(
                "tsb_create_expense_with_idempotency",
                {
                    "p_expense_id": expense_id,
                    "p_trip_id": trip_id,
                    "p_owner_id": user_id,
                    "p_occurred_at": body.occurred_at.isoformat(),
                    "p_payload": body.payload,
                    "p_key": idempotency_key,
                    "p_response": response,
                },
            )
        except (CloudBasePgConfigurationError, CloudBasePgUnavailable) as exc:
            raise _cloudbase_error(exc) from exc
        if not isinstance(result, dict) or result.get("not_found"):
            raise _not_found()
        persisted = result.get("response", response)
        return JSONResponse(status_code=status.HTTP_201_CREATED, content=persisted)

    trip = session.scalar(select(Trip).where(Trip.id == trip_id, Trip.owner_id == user_id))
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
        occurred_at=body.occurred_at,
        payload_json=body.payload,
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


@router.patch("/trips/{trip_id}/expenses/{expense_id}")
async def update_expense(
    trip_id: str,
    expense_id: str,
    body: UpdateExpenseRequest,
    http_request: Request,
    session: DbSession,
    user_id: CurrentUser,
) -> dict[str, dict[str, object]]:
    cloudbase = _cloudbase(http_request)
    if cloudbase is not None:
        try:
            result = await cloudbase.rpc(
                "tsb_update_expense_revision",
                {
                    "p_expense_id": expense_id,
                    "p_trip_id": trip_id,
                    "p_owner_id": user_id,
                    "p_revision": body.revision,
                    "p_occurred_at": body.occurred_at.isoformat(),
                    "p_payload": body.payload,
                },
            )
        except (CloudBasePgConfigurationError, CloudBasePgUnavailable) as exc:
            raise _cloudbase_error(exc) from exc
        if not isinstance(result, dict):
            raise _cloudbase_error(ValueError("invalid CloudBase update response"))
        typed_result = cast(dict[str, Any], result)
        return {
            "data": {
                "id": str(typed_result["id"]),
                "revision": int(typed_result["revision"]),
                "occurred_at": str(typed_result["occurred_at"]),
                "payload": typed_result["payload"],
            }
        }

    trip = session.scalar(select(Trip).where(Trip.id == trip_id, Trip.owner_id == user_id))
    if trip is None:
        raise _not_found()
    expense = session.scalar(
        select(ExpenseRecord).where(
            ExpenseRecord.id == expense_id,
            ExpenseRecord.trip_id == trip_id,
            ExpenseRecord.owner_id == user_id,
        )
    )
    if expense is None:
        raise _not_found()
    result = session.execute(
        update(ExpenseRecord)
        .where(
            ExpenseRecord.id == expense_id,
            ExpenseRecord.owner_id == user_id,
            ExpenseRecord.trip_id == trip_id,
            ExpenseRecord.revision == body.revision,
        )
        .values(
            occurred_at=body.occurred_at,
            payload_json=body.payload,
            revision=body.revision + 1,
        )
    )
    if getattr(result, "rowcount", 0) != 1:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "REVISION_CONFLICT", "message": "消费记录已被更新"},
        )
    session.commit()
    updated = session.scalar(
        select(ExpenseRecord).where(
            ExpenseRecord.id == expense_id,
            ExpenseRecord.trip_id == trip_id,
            ExpenseRecord.owner_id == user_id,
        )
    )
    if updated is None:  # pragma: no cover
        raise _not_found()
    return {
        "data": {
            "id": updated.id,
            "revision": updated.revision,
            "occurred_at": updated.occurred_at.isoformat(),
            "payload": updated.payload_json,
        }
    }
