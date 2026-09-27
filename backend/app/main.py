from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api.v1.auth import router as auth_router
from app.api.v1.settlements import router as settlements_router
from app.api.v1.shares import router as shares_router
from app.api.v1.trip_invites import router as trip_invites_router
from app.api.v1.trips import router as trips_router
from app.api.v1.uploads import router as uploads_router
from app.providers.cloudbase_pg import (
    CloudBasePgClient,
    CloudBasePgConfigurationError,
    CloudBasePgRequestError,
    CloudBasePgUnavailable,
)
from app.providers.cloudbase_receipt_ai import (
    CloudBaseReceiptAi,
    CloudBaseReceiptAiError,
)
from app.providers.wechat_auth import WechatAuthError, WechatCode2Session


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_wechat_auth()
    configure_cloudbase_pg()
    configure_receipt_ai()
    yield


app = FastAPI(lifespan=lifespan)


def configure_cloudbase_pg() -> None:
    if getattr(app.state, "cloudbase_pg", None) is not None:
        return
    try:
        app.state.cloudbase_pg = CloudBasePgClient.from_environment()
    except CloudBasePgConfigurationError:
        app.state.cloudbase_pg = None

def configure_wechat_auth() -> None:
    """Attach the production WeChat code2Session provider when configured."""
    if getattr(app.state, "wechat_auth", None) is not None:
        return
    try:
        app.state.wechat_auth = WechatCode2Session.from_environment()
    except WechatAuthError:
        # Local development and health endpoints remain available without WeChat credentials.
        return


def configure_receipt_ai() -> None:
    if getattr(app.state, "receipt_ai", None) is not None:
        return
    try:
        app.state.receipt_ai = CloudBaseReceiptAi.from_environment()
    except CloudBaseReceiptAiError:
        app.state.receipt_ai = None

app.include_router(auth_router)
app.include_router(trips_router)
app.include_router(trip_invites_router)
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
async def readyz() -> dict[str, str]:
    client: CloudBasePgClient | None = getattr(app.state, "cloudbase_pg", None)
    if client is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "DATABASE_CONFIGURATION_INVALID",
                "message": "CloudBase PostgreSQL is not configured",
            },
        )
    try:
        await client.request("GET", "/trips", params={"select": "id", "limit": "1"})
    except CloudBasePgConfigurationError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "DATABASE_CONFIGURATION_INVALID", "message": "CloudBase PostgreSQL authorization failed"},
        ) from exc
    except CloudBasePgRequestError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "DATABASE_REQUEST_REJECTED",
                "message": f"CloudBase PostgreSQL rejected the readiness check (HTTP {exc.status_code})",
            },
        ) from exc
    except CloudBasePgUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "DATABASE_UNAVAILABLE", "message": "CloudBase PostgreSQL is unavailable"},
        ) from exc
    return {"status": "ready"}
