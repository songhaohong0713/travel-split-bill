import logging
from typing import Annotated, Any, cast
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import select

from app.api.dependencies import CurrentUser, DbSession
from app.api.v1.trips import _not_found
from app.db.models import ReceiptImage, ReceiptJob, Trip
from app.providers.cloudbase_pg import (
    CloudBasePgClient,
    CloudBasePgConfigurationError,
    CloudBasePgUnavailable,
)
from app.providers.deepseek_receipt_ai import DeepSeekReceiptAiError

router = APIRouter(prefix="/v1", tags=["uploads"])
logger = logging.getLogger(__name__)


def _cloudbase(request: Request) -> CloudBasePgClient | None:
    return getattr(request.app.state, "cloudbase_pg", None)


def _cloudbase_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=503, detail={"code": "DATABASE_UNAVAILABLE", "message": "CloudBase PostgreSQL is unavailable"})


@router.post("/receipt-jobs", status_code=status.HTTP_202_ACCEPTED)
async def create_receipt_job(
    trip_id: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    http_request: Request,
    session: DbSession,
    user_id: CurrentUser,
) -> dict[str, object]:
    if file.content_type != "image/jpeg":
        raise HTTPException(status_code=400, detail={"code": "INVALID_RECEIPT_IMAGE", "message": "仅支持 JPEG 小票"})
    image_bytes = await file.read()
    if not image_bytes or len(image_bytes) > 5_000_000:
        raise HTTPException(status_code=400, detail={"code": "INVALID_RECEIPT_IMAGE", "message": "小票图片必须小于 5 MB"})
    image_id, job_id = str(uuid4()), str(uuid4())
    key = f"receipts/{user_id}/{trip_id}/{image_id}.jpg"
    cloudbase = _cloudbase(http_request)
    if cloudbase is not None:
        try:
            image_result = await cloudbase.rpc("tsb_create_receipt_image", {"p_image_id": image_id, "p_owner_id": user_id, "p_trip_id": trip_id, "p_object_key": key})
            if not isinstance(image_result, dict) or image_result.get("not_found"):
                raise _not_found()
            await cloudbase.rpc("tsb_mark_receipt_uploaded", {"p_image_id": image_id, "p_owner_id": user_id})
            result = await cloudbase.rpc("tsb_create_receipt_job", {"p_job_id": job_id, "p_image_id": image_id, "p_owner_id": user_id})
        except (CloudBasePgConfigurationError, CloudBasePgUnavailable) as exc:
            raise _cloudbase_error(exc) from exc
        if not isinstance(result, dict) or result.get("not_found"):
            raise _not_found()
    else:
        trip = session.scalar(select(Trip).where(Trip.id == trip_id, Trip.owner_id == user_id))
        if trip is None:
            raise _not_found()
        session.add(ReceiptImage(id=image_id, owner_id=user_id, trip_id=trip_id, object_key=key, status="uploaded"))
        session.add(ReceiptJob(id=job_id, image_id=image_id))
        session.commit()

    provider = getattr(http_request.app.state, "receipt_ai", None)
    status_value, attempts, candidates, error_code = await _recognize(provider, image_bytes)
    logger.info(
        "receipt_job recognition_finished status=%s attempts=%s candidates=%s",
        status_value,
        attempts,
        len(candidates),
    )
    if cloudbase is not None:
        try:
            await cloudbase.request(
                "PATCH",
                "/receipt_jobs",
                params={"id": f"eq.{job_id}"},
                payload={"status": status_value, "attempts": attempts, "candidates_json": candidates, "error_code": error_code},
            )
        except (CloudBasePgConfigurationError, CloudBasePgUnavailable) as exc:
            raise _cloudbase_error(exc) from exc
    else:
        job = session.get(ReceiptJob, job_id)
        if job is None:
            raise _not_found()
        job.status, job.attempts, job.candidates_json, job.error_code = status_value, attempts, candidates, error_code
        session.commit()
    return {"data": {"id": job_id, "status": status_value, "candidates": candidates}}


async def _recognize(provider: object, image_bytes: bytes) -> tuple[str, int, list[dict[str, str]], str | None]:
    if provider is None:
        return "failed", 0, [], "OCR_FAILED"
    for attempts in range(1, 4):
        try:
            candidates = await provider.recognize(image_bytes)
            return "needs_review", attempts, candidates, None
        except DeepSeekReceiptAiError as exc:
            logger.warning(
                "receipt_job recognition_attempt_failed attempt=%s error_type=%s",
                attempts,
                type(exc).__name__,
            )
            continue
    return "failed", 3, [], "OCR_FAILED"


@router.get("/receipt-jobs/{job_id}")
async def get_receipt_job(
    job_id: str, http_request: Request, session: DbSession, user_id: CurrentUser
) -> dict[str, object]:
    cloudbase = _cloudbase(http_request)
    if cloudbase is not None:
        try:
            rows = await cloudbase.request("GET", "/receipt_jobs", params={"id": f"eq.{job_id}", "select": "id,image_id,status,attempts,candidates_json,error_code", "limit": "1"})
            if not isinstance(rows, list) or not rows:
                raise _not_found()
            job_row = cast(dict[str, Any], rows[0])
            images = await cloudbase.request("GET", "/receipt_images", params={"id": f"eq.{job_row['image_id']}", "owner_id": f"eq.{user_id}", "select": "id", "limit": "1"})
        except (CloudBasePgConfigurationError, CloudBasePgUnavailable) as exc:
            raise _cloudbase_error(exc) from exc
        if not isinstance(images, list) or not images:
            raise _not_found()
        return {"data": {"id": str(job_row["id"]), "status": str(job_row["status"]), "attempts": int(job_row["attempts"]), "candidates": job_row.get("candidates_json") or [], "error_code": job_row.get("error_code")}}
    job = session.get(ReceiptJob, job_id)
    if job is None:
        raise _not_found()
    image = session.get(ReceiptImage, job.image_id)
    if image is None or image.owner_id != user_id:
        raise _not_found()
    return {"data": {"id": job.id, "status": job.status, "attempts": job.attempts, "candidates": job.candidates_json or [], "error_code": job.error_code}}
