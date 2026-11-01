from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, field_validator

from app.api.dependencies import CurrentUser, DbSession
from app.api.v1.trips import _cloudbase, _cloudbase_error, _ensure_user
from app.db.models import User
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
