from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.v1.auth import router as auth_router
from app.api.v1.settlements import router as settlements_router
from app.api.v1.shares import router as shares_router
from app.api.v1.trips import router as trips_router
from app.api.v1.uploads import router as uploads_router
from app.db import session as database
from app.providers.wechat_auth import WechatAuthError, WechatCode2Session


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_wechat_auth()
    yield


app = FastAPI(lifespan=lifespan)


def configure_wechat_auth() -> None:
    """Attach the production WeChat code2Session provider when configured."""
    if getattr(app.state, "wechat_auth", None) is not None:
        return
    try:
        app.state.wechat_auth = WechatCode2Session.from_environment()
    except WechatAuthError:
        # Local development and health endpoints remain available without WeChat credentials.
        return

app.include_router(auth_router)
app.include_router(trips_router)
app.include_router(settlements_router)
app.include_router(shares_router)
app.include_router(uploads_router)


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        error = exc.detail
    else:
        error = {"code": "HTTP_ERROR", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": error})


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict[str, str]:
    try:
        with database.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "DATABASE_UNAVAILABLE", "message": "Database is unavailable"},
        ) from exc
    return {"status": "ready"}