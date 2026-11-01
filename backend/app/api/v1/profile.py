from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.api.dependencies import CurrentUser, DbSession
from app.api.v1.trips import (
    _cloudbase,
    _cloudbase_error,
    _ensure_user,
    require_trip_member,
)
from app.db.models import TripMember, User
from app.providers.cloudbase_pg import (
    CloudBasePgConfigurationError,
    CloudBasePgRequestError,
    CloudBasePgUnavailable,
)

router = APIRouter(prefix="/v1", tags=["profile"])


class NicknameRequest(BaseModel):
    nickname: str = Field(min_length=1, max_length=40)

    @field_validator("nickname")
    @classmethod
    def trimmed(cls, value):
        if not value.strip():
            raise ValueError("请输入昵称")
        return value.strip()


@router.patch("/trips/{trip_id}/members/me")
async def update_trip_nickname(
    trip_id: str,
    body: NicknameRequest,
    request: Request,
    session: DbSession,
    user_id: CurrentUser,
):
    cloud = _cloudbase(request)
    if cloud is not None:
        try:
            rows = await cloud.request(
                "GET",
                "/trip_members",
                params={
                    "trip_id": f"eq.{trip_id}",
                    "user_id": f"eq.{user_id}",
                    "select": "user_id",
                },
            )
            if rows:
                await cloud.request(
                    "PATCH",
                    "/trip_members",
                    params={"trip_id": f"eq.{trip_id}", "user_id": f"eq.{user_id}"},
                    payload={"nickname": body.nickname},
                )
        except (
            CloudBasePgConfigurationError,
            CloudBasePgRequestError,
            CloudBasePgUnavailable,
        ) as exc:
            raise _cloudbase_error(exc) from exc
        if not rows:
            raise HTTPException(
                404, detail={"code": "NOT_FOUND", "message": "旅行不存在或你尚未加入"}
            )
    else:
        require_trip_member(session, trip_id, user_id)
        member = session.scalar(
            select(TripMember).where(
                TripMember.trip_id == trip_id, TripMember.user_id == user_id
            )
        )
        if member is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "旅行不存在或你尚未加入"})
        member.nickname = body.nickname
        session.commit()
    return {"data": {"id": user_id, "nickname": body.nickname}}


@router.patch("/me")
async def update_profile(
    body: NicknameRequest, request: Request, session: DbSession, user_id: CurrentUser
):
    cloud = _cloudbase(request)
    if cloud is not None:
        try:
            await cloud.request(
                "PATCH",
                "/users",
                params={"id": f"eq.{user_id}"},
                payload={"nickname": body.nickname},
            )
        except (
            CloudBasePgConfigurationError,
            CloudBasePgRequestError,
            CloudBasePgUnavailable,
        ) as exc:
            raise _cloudbase_error(exc) from exc
    else:
        _ensure_user(session, user_id)
        user = session.get(User, user_id)
        assert user is not None
        user.nickname = body.nickname
        session.commit()
    return {"data": {"id": user_id, "nickname": body.nickname}}
