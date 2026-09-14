import hashlib
import logging
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from jwt import InvalidTokenError
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import RefreshToken, User
from app.db.session import get_session
from app.providers.wechat_auth import WechatAuthError

JWT_ALGORITHM = "HS256"
JWT_SECRET = os.getenv("JWT_SECRET", "development-only-secret-key-not-prod-32")
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/auth", tags=["auth"])


class WechatLoginRequest(BaseModel):
    code: str = Field(min_length=1, max_length=512)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=512)


def create_access_token(user_id: str) -> str:
    expires_at = datetime.now(UTC) + timedelta(minutes=30)
    return jwt.encode(
        {"sub": user_id, "exp": expires_at}, JWT_SECRET, algorithm=JWT_ALGORITHM
    )


def decode_access_token(token: str) -> str:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except InvalidTokenError as exc:
        raise ValueError("invalid access token") from exc
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise ValueError("invalid access token subject")
    return subject


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _issue_tokens(session: Session, user_id: str) -> dict[str, str]:
    refresh_token = secrets.token_urlsafe(32)
    session.add(
        RefreshToken(
            token_hash=_hash(refresh_token),
            user_id=user_id,
            expires_at=_now() + timedelta(days=30),
        )
    )
    return {
        "access_token": create_access_token(user_id),
        "refresh_token": refresh_token,
    }


@router.post("/wechat")
async def wechat_login(
    request: Request,
    body: WechatLoginRequest,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, dict[str, str]]:
    provider: Any | None = getattr(request.app.state, "wechat_auth", None)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="wechat login unavailable",
        )
    try:
        openid = await provider.openid_for_code(body.code)
    except (WechatAuthError, httpx.HTTPError) as exc:
        logger.warning("WeChat code2Session failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "WECHAT_LOGIN_FAILED", "message": "微信登录验证失败，请检查服务配置"},
        ) from exc
    openid_hash = _hash(openid)
    user = session.scalar(select(User).where(User.wechat_openid_hash == openid_hash))
    if user is None:
        user = User(id=secrets.token_urlsafe(18), wechat_openid_hash=openid_hash)
        session.add(user)
        session.flush()
    tokens = _issue_tokens(session, user.id)
    session.commit()
    return {"data": tokens}


@router.post("/refresh")
def refresh(
    body: RefreshRequest, session: Annotated[Session, Depends(get_session)]
) -> dict[str, dict[str, str]]:
    record = session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == _hash(body.refresh_token))
    )
    if record is None or record.revoked_at is not None or record.expires_at <= _now():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid refresh token"
        )
    record.revoked_at = _now()
    tokens = _issue_tokens(session, record.user_id)
    session.commit()
    return {"data": tokens}
