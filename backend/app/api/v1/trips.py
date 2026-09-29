import json
from datetime import date
from typing import Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update

from app.api.dependencies import CurrentUser, DbSession
from app.db.models import (
    ExpenseRecord,
    IdempotencyRecord,
    ReceiptImage,
    ReceiptJob,
    SettlementVersion,
    ShareLink,
    Trip,
    TripInvite,
    TripMember,
    User,
)
from app.providers.cloudbase_pg import (
    CloudBasePgClient,
    CloudBasePgConfigurationError,
    CloudBasePgRequestError,
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

def require_trip_member(session: DbSession, trip_id: str, user_id: str) -> Trip:
    trip = session.scalar(select(Trip).join(TripMember).where(Trip.id == trip_id, TripMember.user_id == user_id))
    if trip is None:
        raise _not_found()
    return trip


def require_trip_owner(session: DbSession, trip_id: str, user_id: str) -> Trip:
    trip = session.scalar(select(Trip).where(Trip.id == trip_id, Trip.owner_id == user_id))
    if trip is None:
        raise _not_found()
    return trip

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
    session.flush()
    session.add(TripMember(trip_id=trip.id, user_id=user_id))
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
) -> dict[str, list[dict[str, object]]]:
    cloudbase = _cloudbase(http_request)
    if cloudbase is not None:
        try:
            rows = await cloudbase.rpc("tsb_list_member_trips", {"p_user_id": user_id})
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
                    "member_count": int(row.get("member_count", 1)),
                    "is_owner": bool(row.get("is_owner", False)),
                }
                for row in typed_rows
            ]
        }

    trips = session.scalars(select(Trip).join(TripMember).where(TripMember.user_id == user_id).order_by(Trip.created_at, Trip.id)).all()
    return {"data": [{"id": trip.id, "name": trip.name, "default_currency": trip.default_currency, "member_count": session.scalar(select(func.count(TripMember.id)).where(TripMember.trip_id == trip.id)), "is_owner": trip.owner_id == user_id} for trip in trips]}


@router.get("/trips/{trip_id}/members")
async def list_trip_members(trip_id: str, http_request: Request, session: DbSession, user_id: CurrentUser) -> dict[str, list[dict[str, object]]]:
    cloudbase = _cloudbase(http_request)
    if cloudbase is not None:
        try:
            memberships = await cloudbase.rpc("tsb_list_member_trips", {"p_user_id": user_id})
            trip = next((row for row in memberships if isinstance(row, dict) and str(row.get("id", "")) == trip_id), None) if isinstance(memberships, list) else None
            if trip is None:
                raise _not_found()
            rows = await cloudbase.request(
                "GET", "/trip_members",
                params={"trip_id": f"eq.{trip_id}", "select": "user_id,joined_at", "order": "joined_at.asc"},
            )
        except (CloudBasePgConfigurationError, CloudBasePgRequestError, CloudBasePgUnavailable) as exc:
            raise _cloudbase_error(exc) from exc
        if not isinstance(rows, list):
            raise _cloudbase_error(ValueError("invalid CloudBase member response"))
        owner_id = user_id if bool(trip.get("is_owner", False)) else next((str(row["user_id"]) for row in rows if str(row.get("user_id", "")) != user_id), "")
        return {"data": [{"id": str(row["user_id"]), "is_owner": str(row["user_id"]) == owner_id, "is_current": str(row["user_id"]) == user_id} for row in rows]}
    trip = require_trip_member(session, trip_id, user_id)
    members = session.scalars(select(TripMember).where(TripMember.trip_id == trip.id).order_by(TripMember.joined_at)).all()
    return {"data": [{"id": member.user_id, "is_owner": member.user_id == trip.owner_id, "is_current": member.user_id == user_id} for member in members]}

@router.get("/trips/{trip_id}/expenses")
async def list_expenses(
    trip_id: str, http_request: Request, session: DbSession, user_id: CurrentUser
) -> dict[str, list[dict[str, object]]]:
    cloudbase = _cloudbase(http_request)
    if cloudbase is not None:
        try:
            memberships = await cloudbase.rpc("tsb_list_member_trips", {"p_user_id": user_id})
            if not isinstance(memberships, list) or not any(str(row.get("id", "")) == trip_id for row in memberships if isinstance(row, dict)):
                raise _not_found()
            rows = await cloudbase.request(
                "GET",
                "/expenses",
                params={
                    "trip_id": f"eq.{trip_id}",
                    "select": "id,owner_id,revision,occurred_at,payload_json",
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
                    "is_creator": str(row.get("owner_id", "")) == user_id,
                }
                for row in typed_rows
            ]
        }

    trip = require_trip_member(session, trip_id, user_id)
    expenses = session.scalars(
        select(ExpenseRecord)
        .where(ExpenseRecord.trip_id == trip_id)
        .order_by(ExpenseRecord.created_at)
    ).all()
    return {
        "data": [
            {
                "id": expense.id,
                "revision": expense.revision,
                "occurred_at": expense.occurred_at.isoformat(),
                "payload": expense.payload_json,
                "is_creator": expense.owner_id == user_id,
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

    trip = require_trip_member(session, trip_id, user_id)
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
        except CloudBasePgRequestError as exc:
            if "TSB_REVISION_CONFLICT" in str(exc):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "REVISION_CONFLICT", "message": "消费记录已被更新"},
                ) from exc
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

    trip = require_trip_member(session, trip_id, user_id)
    expense = session.scalar(
        select(ExpenseRecord).where(
            ExpenseRecord.id == expense_id,
            ExpenseRecord.trip_id == trip_id,
        )
    )
    if expense is None:
        raise _not_found()
    if expense.owner_id != user_id and trip.owner_id != user_id:
        raise _not_found()
    result = session.execute(
        update(ExpenseRecord)
        .where(
            ExpenseRecord.id == expense_id,
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


@router.delete("/trips/{trip_id}/expenses/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_expense(
    trip_id: str, expense_id: str, http_request: Request, session: DbSession, user_id: CurrentUser
) -> Response:
    cloudbase = _cloudbase(http_request)
    if cloudbase is not None:
        try:
            result = await cloudbase.rpc(
                "tsb_delete_owner_expense",
                {"p_trip_id": trip_id, "p_expense_id": expense_id, "p_owner_id": user_id},
            )
        except (CloudBasePgConfigurationError, CloudBasePgUnavailable, CloudBasePgRequestError) as exc:
            raise _cloudbase_error(exc) from exc
        if not isinstance(result, dict) or result.get("not_found"):
            raise _not_found()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    trip = require_trip_member(session, trip_id, user_id)
    expense = session.scalar(select(ExpenseRecord).where(ExpenseRecord.id == expense_id, ExpenseRecord.trip_id == trip_id))
    if expense is None or (expense.owner_id != user_id and trip.owner_id != user_id):
        raise _not_found()
    result = session.execute(delete(ExpenseRecord).where(ExpenseRecord.id == expense_id, ExpenseRecord.trip_id == trip_id))
    if getattr(result, "rowcount", 0) != 1:
        raise _not_found()
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/trips/{trip_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_trip(
    trip_id: str, http_request: Request, session: DbSession, user_id: CurrentUser
) -> Response:
    cloudbase = _cloudbase(http_request)
    if cloudbase is not None:
        try:
            result = await cloudbase.rpc("tsb_delete_owner_trip", {"p_trip_id": trip_id, "p_owner_id": user_id})
        except (CloudBasePgConfigurationError, CloudBasePgUnavailable, CloudBasePgRequestError) as exc:
            raise _cloudbase_error(exc) from exc
        if not isinstance(result, dict) or result.get("not_found"):
            raise _not_found()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    require_trip_owner(session, trip_id, user_id)
    image_ids = select(ReceiptImage.id).where(ReceiptImage.trip_id == trip_id)
    session.execute(delete(ReceiptJob).where(ReceiptJob.image_id.in_(image_ids)))
    session.execute(delete(ReceiptImage).where(ReceiptImage.trip_id == trip_id))
    session.execute(delete(ShareLink).where(ShareLink.trip_id == trip_id))
    session.execute(delete(SettlementVersion).where(SettlementVersion.trip_id == trip_id))
    session.execute(delete(TripInvite).where(TripInvite.trip_id == trip_id))
    session.execute(delete(ExpenseRecord).where(ExpenseRecord.trip_id == trip_id))
    session.execute(delete(TripMember).where(TripMember.trip_id == trip_id))
    session.execute(delete(Trip).where(Trip.id == trip_id))
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
