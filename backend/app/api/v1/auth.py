import os
from datetime import UTC, datetime, timedelta

import jwt
from jwt import InvalidTokenError

JWT_ALGORITHM = "HS256"
JWT_SECRET = os.getenv("JWT_SECRET", "development-only-secret-key-not-prod-32")


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
