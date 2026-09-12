from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api.v1.auth import router as auth_router
from app.api.v1.trips import router as trips_router

app = FastAPI()
app.include_router(auth_router)
app.include_router(trips_router)


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
