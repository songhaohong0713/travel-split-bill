import json
from datetime import date
from typing import Any, cast
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import delete, func, select, update

from app.api.dependencies import CurrentUser, DbSession
from app.core.expense_validation import validate_expense_payload
from app.core.money import currency_scale
from app.core.receipt_files import (
    cleanup_after_request,
    cleanup_unreferenced,
    queue_image,
    validate_receipt_reference,
)
from app.db.models import (
    ExpenseRecord,
    IdempotencyRecord,
    PaymentAllocationRecord,
    PaymentRecord,
    ReceiptImage,
    ReceiptJob,
    SettlementVersion,
    ShareLink,
    Trip,
    TripInvite,
    TripLedger,
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
    client_id: UUID | None = None
    name: str = Field(min_length=1, max_length=120)
    default_currency: str = Field(pattern=r"^[A-Z]{3}$")

    @field_validator("default_currency")
    @classmethod
    def supported_currency(cls, value):
        currency_scale(value)
        return value


class CreateExpenseRequest(BaseModel):
    occurred_at: date
    payload: dict[str, object]


class TripDatesRequest(BaseModel):
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def check_dates(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("结束日期不能早于开始日期")
        return self


class TripNameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value):
        if not value.strip():
            raise ValueError("请输入旅行名称")
        return value.strip()


@router.patch("/trips/{trip_id}/name")
async def update_trip_name(
    trip_id: str,
    body: TripNameRequest,
    request: Request,
    session: DbSession,
    user_id: CurrentUser,
):
    cloud = _cloudbase(request)
    if cloud is not None:
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
            if not rows:
                raise _not_found()
            await cloud.request(
                "PATCH",
                "/trips",
                params={"id": f"eq.{trip_id}", "owner_id": f"eq.{user_id}"},
                payload={"name": body.name},
            )
        except (
            CloudBasePgConfigurationError,
            CloudBasePgRequestError,
            CloudBasePgUnavailable,
        ) as exc:
            raise _cloudbase_error(exc) from exc
    else:
        trip = require_trip_owner(session, trip_id, user_id)
        trip.name = body.name
        session.commit()
    return {"data": {"name": body.name}}


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
        detail={
            "code": "DATABASE_UNAVAILABLE",
            "message": "CloudBase PostgreSQL is unavailable",
        },
    )


def _ensure_user(session: DbSession, user_id: str) -> None:
    if session.get(User, user_id) is None:
        session.add(User(id=user_id, wechat_openid_hash=None))
        session.flush()


def _cloudbase(request: Request) -> CloudBasePgClient | None:
    return getattr(request.app.state, "cloudbase_pg", None)


async def _validate_expense(trip_id, payload, request, session, user_id):
    cloud = _cloudbase(request)
    if cloud is not None:
        try:
            rows = await cloud.request(
                "GET",
                "/trip_members",
                params={"trip_id": f"eq.{trip_id}", "select": "user_id"},
            )
        except (
            CloudBasePgConfigurationError,
            CloudBasePgRequestError,
            CloudBasePgUnavailable,
        ) as exc:
            raise _cloudbase_error(exc) from exc
        members = (
            {str(row["user_id"]) for row in rows} if isinstance(rows, list) else set()
        )
        if user_id not in members:
            raise _not_found()
        try:
            trips = await cloud.request(
                "GET",
                "/trips",
                params={
                    "id": f"eq.{trip_id}",
                    "select": "default_currency",
                    "limit": "1",
                },
            )
        except (
            CloudBasePgConfigurationError,
            CloudBasePgRequestError,
            CloudBasePgUnavailable,
        ) as exc:
            raise _cloudbase_error(exc) from exc
        if not isinstance(trips, list) or not trips:
            raise _not_found()
        currency = str(trips[0]["default_currency"])
    else:
        trip = require_trip_member(session, trip_id, user_id)
        members = set(
            session.scalars(
                select(TripMember.user_id).where(TripMember.trip_id == trip_id)
            ).all()
        )
        currency = trip.default_currency
    try:
        validate_expense_payload(payload, members, currency)
    except (ValueError, ArithmeticError, TypeError, KeyError) as exc:
        raise HTTPException(
            422,
            detail={
                "code": "INVALID_EXPENSE",
                "message": "消费数据不完整或无效，请核对成员、金额、分摊和汇率",
            },
        ) from exc


def require_trip_member(session: DbSession, trip_id: str, user_id: str) -> Trip:
    trip = session.scalar(
        select(Trip)
        .join(TripMember)
        .where(Trip.id == trip_id, TripMember.user_id == user_id)
        .with_for_update(of=Trip)
    )
    if trip is None:
        raise _not_found()
    return trip


def require_trip_owner(session: DbSession, trip_id: str, user_id: str) -> Trip:
    trip = session.scalar(
        select(Trip)
        .where(Trip.id == trip_id, Trip.owner_id == user_id)
        .with_for_update()
    )
    if trip is None:
        raise _not_found()
    return trip


@router.get("/capabilities")
async def capabilities(user_id: CurrentUser):
    return {
        "data": {
            "client_trip_id": True,
            "settlement_flow": "recipient_confirmation",
            "calculation_version": "v2",
        }
    }


@router.post("/trips", status_code=status.HTTP_201_CREATED)
async def create_trip(
    body: CreateTripRequest,
    http_request: Request,
    session: DbSession,
    user_id: CurrentUser,
) -> dict[str, dict[str, str]]:
    cloudbase = _cloudbase(http_request)
    if cloudbase is not None:
        trip_id = str(body.client_id or uuid4())
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
        if result.get("conflict"):
            raise HTTPException(
                409,
                detail={
                    "code": "TRIP_ID_CONFLICT",
                    "message": "Trip identifier already exists with different data",
                },
            )
        typed_result = cast(dict[str, Any], result)
        return {
            "data": {
                "id": str(typed_result["id"]),
                "name": str(typed_result["name"]),
                "default_currency": str(typed_result["default_currency"]),
            }
        }

    if body.client_id is not None:
        existing = session.get(Trip, str(body.client_id))
        if existing is not None:
            if (
                existing.owner_id != user_id
                or existing.name != body.name
                or existing.default_currency != body.default_currency
            ):
                raise HTTPException(
                    409,
                    detail={
                        "code": "TRIP_ID_CONFLICT",
                        "message": "Trip identifier already exists with different data",
                    },
                )
            return {
                "data": {
                    "id": existing.id,
                    "name": existing.name,
                    "default_currency": existing.default_currency,
                }
            }
    _ensure_user(session, user_id)
    trip = Trip(
        id=str(body.client_id or uuid4()),
        owner_id=user_id,
        name=body.name,
        default_currency=body.default_currency,
    )
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
        try:
            date_rows = (
                await cloudbase.request(
                    "GET",
                    "/trips",
                    params={
                        "id": "in.("
                        + ",".join(str(row["id"]) for row in typed_rows)
                        + ")",
                        "select": "id,start_date,end_date",
                    },
                )
                if typed_rows
                else []
            )
        except (
            CloudBasePgConfigurationError,
            CloudBasePgRequestError,
            CloudBasePgUnavailable,
        ) as exc:
            raise _cloudbase_error(exc) from exc
        dates = (
            {str(row["id"]): row for row in date_rows}
            if isinstance(date_rows, list)
            else {}
        )
        return {
            "data": [
                {
                    "id": str(row["id"]),
                    "name": str(row["name"]),
                    "default_currency": str(row["default_currency"]),
                    "member_count": int(row.get("member_count", 1)),
                    "is_owner": bool(row.get("is_owner", False)),
                    "start_date": dates.get(str(row["id"]), {}).get("start_date"),
                    "end_date": dates.get(str(row["id"]), {}).get("end_date"),
                }
                for row in typed_rows
            ]
        }

    trips = session.scalars(
        select(Trip)
        .join(TripMember)
        .where(TripMember.user_id == user_id)
        .order_by(Trip.created_at, Trip.id)
    ).all()
    return {
        "data": [
            {
                "id": trip.id,
                "name": trip.name,
                "default_currency": trip.default_currency,
                "start_date": str(trip.start_date) if trip.start_date else None,
                "end_date": str(trip.end_date) if trip.end_date else None,
                "member_count": session.scalar(
                    select(func.count(TripMember.id)).where(
                        TripMember.trip_id == trip.id
                    )
                ),
                "is_owner": trip.owner_id == user_id,
            }
            for trip in trips
        ]
    }


@router.patch("/trips/{trip_id}")
async def update_trip_dates(
    trip_id: str,
    body: TripDatesRequest,
    request: Request,
    session: DbSession,
    user_id: CurrentUser,
):
    cloud = _cloudbase(request)
    if cloud is not None:
        try:
            result = await cloud.rpc(
                "tsb_update_trip_dates",
                {
                    "p_trip_id": trip_id,
                    "p_user_id": user_id,
                    "p_start_date": body.start_date.isoformat()
                    if body.start_date
                    else None,
                    "p_end_date": body.end_date.isoformat() if body.end_date else None,
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
    else:
        trip = require_trip_owner(session, trip_id, user_id)
        trip.start_date, trip.end_date = body.start_date, body.end_date
        session.commit()
    return {"data": body.model_dump(mode="json")}


@router.get("/trips/{trip_id}/members")
async def list_trip_members(
    trip_id: str, http_request: Request, session: DbSession, user_id: CurrentUser
) -> dict[str, list[dict[str, object]]]:
    cloudbase = _cloudbase(http_request)
    if cloudbase is not None:
        try:
            memberships = await cloudbase.rpc(
                "tsb_list_member_trips", {"p_user_id": user_id}
            )
            trip = (
                next(
                    (
                        row
                        for row in memberships
                        if isinstance(row, dict) and str(row.get("id", "")) == trip_id
                    ),
                    None,
                )
                if isinstance(memberships, list)
                else None
            )
            if trip is None:
                raise _not_found()
            rows = await cloudbase.request(
                "GET",
                "/trip_members",
                params={
                    "trip_id": f"eq.{trip_id}",
                    "select": "user_id,joined_at",
                    "order": "joined_at.asc",
                },
            )
        except (
            CloudBasePgConfigurationError,
            CloudBasePgRequestError,
            CloudBasePgUnavailable,
        ) as exc:
            raise _cloudbase_error(exc) from exc
        if not isinstance(rows, list):
            raise _cloudbase_error(ValueError("invalid CloudBase member response"))
        owner_id = (
            user_id
            if bool(trip.get("is_owner", False))
            else next(
                (
                    str(row["user_id"])
                    for row in rows
                    if str(row.get("user_id", "")) != user_id
                ),
                "",
            )
        )
        nick_rows = (
            await cloudbase.request(
                "GET",
                "/users",
                params={
                    "id": "in.(" + ",".join(str(row["user_id"]) for row in rows) + ")",
                    "select": "id,nickname",
                },
            )
            if rows
            else []
        )
        nicknames = (
            {str(row["id"]): row.get("nickname") for row in nick_rows}
            if isinstance(nick_rows, list)
            else {}
        )
        return {
            "data": [
                {
                    "id": str(row["user_id"]),
                    "nickname": nicknames.get(str(row["user_id"])),
                    "is_owner": str(row["user_id"]) == owner_id,
                    "is_current": str(row["user_id"]) == user_id,
                }
                for row in rows
            ]
        }
    member_trip = require_trip_member(session, trip_id, user_id)
    members = session.scalars(
        select(TripMember)
        .where(TripMember.trip_id == member_trip.id)
        .order_by(TripMember.joined_at)
    ).all()
    return {
        "data": [
            {
                "id": member.user_id,
                "nickname": (
                    user.nickname
                    if (user := session.get(User, member.user_id))
                    else None
                ),
                "is_owner": member.user_id == member_trip.owner_id,
                "is_current": member.user_id == user_id,
            }
            for member in members
        ]
    }


@router.get("/trips/{trip_id}/expenses")
async def list_expenses(
    trip_id: str, http_request: Request, session: DbSession, user_id: CurrentUser
) -> dict[str, list[dict[str, object]]]:
    cloudbase = _cloudbase(http_request)
    if cloudbase is not None:
        try:
            memberships = await cloudbase.rpc(
                "tsb_list_member_trips", {"p_user_id": user_id}
            )
            if not isinstance(memberships, list) or not any(
                str(row.get("id", "")) == trip_id
                for row in memberships
                if isinstance(row, dict)
            ):
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

    require_trip_member(session, trip_id, user_id)
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


@router.get("/trips/{trip_id}/expenses/page")
async def expense_page(
    trip_id: str,
    request: Request,
    session: DbSession,
    user_id: CurrentUser,
    limit: int = Query(default=50, ge=1, le=100),
    after_id: str | None = Query(default=None, max_length=120),
):
    cloud = _cloudbase(request)
    if cloud is not None:
        try:
            membership = await cloud.request(
                "GET",
                "/trip_members",
                params={
                    "trip_id": f"eq.{trip_id}",
                    "user_id": f"eq.{user_id}",
                    "select": "id",
                    "limit": "1",
                },
            )
            if not isinstance(membership, list) or not membership:
                raise _not_found()
            params = {
                "trip_id": f"eq.{trip_id}",
                "select": "id,revision,occurred_at,owner_id,payload_json",
                "order": "id.asc",
                "limit": str(limit + 1),
            }
            if after_id:
                params["id"] = f"gt.{after_id}"
            records = await cloud.request("GET", "/expenses", params=params)
        except (
            CloudBasePgConfigurationError,
            CloudBasePgRequestError,
            CloudBasePgUnavailable,
        ) as exc:
            raise _cloudbase_error(exc) from exc
        if not isinstance(records, list):
            raise _cloudbase_error(ValueError("invalid expense page"))
        rows = [
            {
                "id": r["id"],
                "revision": r["revision"],
                "occurred_at": r["occurred_at"],
                "payload": r["payload_json"],
                "is_creator": r["owner_id"] == user_id,
            }
            for r in records
        ]
    else:
        require_trip_member(session, trip_id, user_id)
        query = (
            select(ExpenseRecord)
            .where(ExpenseRecord.trip_id == trip_id)
            .order_by(ExpenseRecord.id)
            .limit(limit + 1)
        )
        if after_id:
            query = query.where(ExpenseRecord.id > after_id)
        rows = [
            {
                "id": r.id,
                "revision": r.revision,
                "occurred_at": str(r.occurred_at),
                "payload": r.payload_json,
                "is_creator": r.owner_id == user_id,
            }
            for r in session.scalars(query).all()
        ]
    return {
        "data": {
            "items": rows[:limit],
            "next_after_id": rows[limit - 1]["id"] if len(rows) > limit else None,
        }
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

    await _validate_expense(trip_id, body.payload, http_request, session, user_id)
    try:
        await validate_receipt_reference(trip_id, body.payload, _cloudbase(http_request), session)
    except (CloudBasePgConfigurationError, CloudBasePgRequestError, CloudBasePgUnavailable) as exc:
        raise _cloudbase_error(exc) from exc
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

    require_trip_member(session, trip_id, user_id)
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
    background_tasks: BackgroundTasks,
) -> dict[str, dict[str, object]]:
    await _validate_expense(trip_id, body.payload, http_request, session, user_id)
    try:
        await validate_receipt_reference(trip_id, body.payload, _cloudbase(http_request), session)
    except (CloudBasePgConfigurationError, CloudBasePgRequestError, CloudBasePgUnavailable) as exc:
        raise _cloudbase_error(exc) from exc
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
        if result.get("not_found"):
            raise _not_found()
        if not {"id", "revision", "occurred_at", "payload"}.issubset(result):
            raise _cloudbase_error(ValueError("incomplete CloudBase update response"))
        typed_result = cast(dict[str, Any], result)
        background_tasks.add_task(cleanup_after_request, cloudbase, getattr(http_request.app.state, 'receipt_storage', None))
        return {
            "data": {
                "id": str(typed_result["id"]),
                "revision": int(typed_result["revision"]),
                "occurred_at": str(typed_result["occurred_at"]),
                "payload": typed_result["payload"],
            }
        }

    require_trip_member(session, trip_id, user_id)
    expense = session.scalar(
        select(ExpenseRecord).where(
            ExpenseRecord.id == expense_id,
            ExpenseRecord.trip_id == trip_id,
        )
    )
    if expense is None:
        raise _not_found()
    previous_job = expense.payload_json.get('receipt_job_id')
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
    if previous_job != body.payload.get('receipt_job_id'):
        cleanup_unreferenced(session, trip_id, previous_job)
    session.commit()
    background_tasks.add_task(cleanup_after_request, None, getattr(http_request.app.state, 'receipt_storage', None))
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


@router.delete(
    "/trips/{trip_id}/expenses/{expense_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_expense(
    trip_id: str,
    expense_id: str,
    http_request: Request,
    session: DbSession,
    user_id: CurrentUser,
    background_tasks: BackgroundTasks,
) -> Response:
    cloudbase = _cloudbase(http_request)
    if cloudbase is not None:
        try:
            result = await cloudbase.rpc(
                "tsb_delete_owner_expense",
                {
                    "p_trip_id": trip_id,
                    "p_expense_id": expense_id,
                    "p_owner_id": user_id,
                },
            )
        except (
            CloudBasePgConfigurationError,
            CloudBasePgUnavailable,
            CloudBasePgRequestError,
        ) as exc:
            raise _cloudbase_error(exc) from exc
        if not isinstance(result, dict) or result.get("not_found"):
            raise _not_found()
        background_tasks.add_task(cleanup_after_request, cloudbase, getattr(http_request.app.state, 'receipt_storage', None))
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    trip = require_trip_member(session, trip_id, user_id)
    expense = session.scalar(
        select(ExpenseRecord).where(
            ExpenseRecord.id == expense_id, ExpenseRecord.trip_id == trip_id
        )
    )
    if expense is None or (expense.owner_id != user_id and trip.owner_id != user_id):
        raise _not_found()
    previous_job = expense.payload_json.get('receipt_job_id')
    result = session.execute(
        delete(ExpenseRecord).where(
            ExpenseRecord.id == expense_id, ExpenseRecord.trip_id == trip_id
        )
    )
    if getattr(result, "rowcount", 0) != 1:
        raise _not_found()
    cleanup_unreferenced(session, trip_id, previous_job)
    session.commit()
    background_tasks.add_task(cleanup_after_request, None, getattr(http_request.app.state, 'receipt_storage', None))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/trips/{trip_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_trip(
    trip_id: str, http_request: Request, session: DbSession, user_id: CurrentUser, background_tasks: BackgroundTasks
) -> Response:
    cloudbase = _cloudbase(http_request)
    if cloudbase is not None:
        try:
            result = await cloudbase.rpc(
                "tsb_delete_owner_trip", {"p_trip_id": trip_id, "p_owner_id": user_id}
            )
        except (
            CloudBasePgConfigurationError,
            CloudBasePgUnavailable,
            CloudBasePgRequestError,
        ) as exc:
            raise _cloudbase_error(exc) from exc
        if not isinstance(result, dict) or result.get("not_found"):
            raise _not_found()
        background_tasks.add_task(cleanup_after_request, cloudbase, getattr(http_request.app.state, 'receipt_storage', None))
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    require_trip_owner(session, trip_id, user_id)
    for image in session.scalars(select(ReceiptImage).where(ReceiptImage.trip_id == trip_id)).all():
        queue_image(session, image)
    payment_ids = select(PaymentRecord.id).where(PaymentRecord.trip_id == trip_id)
    session.execute(
        delete(PaymentAllocationRecord).where(
            PaymentAllocationRecord.payment_id.in_(payment_ids)
        )
    )
    session.execute(delete(PaymentRecord).where(PaymentRecord.trip_id == trip_id))
    session.execute(delete(TripLedger).where(TripLedger.trip_id == trip_id))
    image_ids = select(ReceiptImage.id).where(ReceiptImage.trip_id == trip_id)
    session.execute(delete(ReceiptJob).where(ReceiptJob.image_id.in_(image_ids)))
    session.execute(delete(ReceiptImage).where(ReceiptImage.trip_id == trip_id))
    session.execute(delete(ShareLink).where(ShareLink.trip_id == trip_id))
    session.execute(
        delete(SettlementVersion).where(SettlementVersion.trip_id == trip_id)
    )
    session.execute(delete(TripInvite).where(TripInvite.trip_id == trip_id))
    session.execute(delete(ExpenseRecord).where(ExpenseRecord.trip_id == trip_id))
    session.execute(delete(TripMember).where(TripMember.trip_id == trip_id))
    session.execute(delete(Trip).where(Trip.id == trip_id))
    session.commit()
    background_tasks.add_task(cleanup_after_request, None, getattr(http_request.app.state, 'receipt_storage', None))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
