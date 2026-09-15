from typing import Any, cast
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.dependencies import CurrentUser, DbSession
from app.api.v1.trips import _not_found
from app.db.models import ReceiptImage, ReceiptJob, Trip
from app.providers.cloudbase_pg import (
    CloudBasePgClient,
    CloudBasePgConfigurationError,
    CloudBasePgUnavailable,
)

router = APIRouter(prefix="/v1", tags=["uploads"])


class UploadRequest(BaseModel):
    trip_id: str
    mime_type: str
    byte_size: int = Field(gt=0, le=10_000_000)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def _cloudbase(request: Request) -> CloudBasePgClient | None:
    return getattr(request.app.state, "cloudbase_pg", None)


def _cloudbase_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=503, detail={"code": "DATABASE_UNAVAILABLE", "message": "CloudBase PostgreSQL is unavailable"})


@router.post("/uploads", status_code=status.HTTP_201_CREATED)
async def create_upload(
    body: UploadRequest, http_request: Request, session: DbSession, user_id: CurrentUser
) -> dict[str, dict[str, str]]:
    if body.mime_type != "image/jpeg":
        raise HTTPException(status_code=400, detail={"code": "INVALID_RECEIPT_IMAGE", "message": "仅支持 JPEG 小票"})
    image_id = str(uuid4())
    key = f"receipts/{user_id}/{body.trip_id}/{image_id}.jpg"
    cloudbase = _cloudbase(http_request)
    if cloudbase is not None:
        try:
            result = await cloudbase.rpc("tsb_create_receipt_image", {"p_image_id": image_id, "p_owner_id": user_id, "p_trip_id": body.trip_id, "p_object_key": key})
        except (CloudBasePgConfigurationError, CloudBasePgUnavailable) as exc:
            raise _cloudbase_error(exc) from exc
        if not isinstance(result, dict) or result.get("not_found"):
            raise _not_found()
        return {"data": {"image_id": image_id, "object_key": key, "upload_url": f"/storage-upload/{key}"}}
    trip = session.scalar(select(Trip).where(Trip.id == body.trip_id, Trip.owner_id == user_id))
    if trip is None:
        raise _not_found()
    session.add(ReceiptImage(id=image_id, owner_id=user_id, trip_id=body.trip_id, object_key=key))
    session.commit()
    return {"data": {"image_id": image_id, "object_key": key, "upload_url": f"/storage-upload/{key}"}}


@router.post("/uploads/{image_id}/complete")
async def complete_upload(
    image_id: str, http_request: Request, session: DbSession, user_id: CurrentUser
) -> dict[str, dict[str, str]]:
    cloudbase = _cloudbase(http_request)
    if cloudbase is not None:
        try:
            result = await cloudbase.rpc("tsb_mark_receipt_uploaded", {"p_image_id": image_id, "p_owner_id": user_id})
        except (CloudBasePgConfigurationError, CloudBasePgUnavailable) as exc:
            raise _cloudbase_error(exc) from exc
        if not isinstance(result, dict) or result.get("not_found"):
            raise _not_found()
        return {"data": {"image_id": image_id, "status": str(result["status"])}}
    image = session.get(ReceiptImage, image_id)
    if image is None or image.owner_id != user_id:
        raise _not_found()
    image.status = "uploaded"
    session.commit()
    return {"data": {"image_id": image.id, "status": image.status}}


class ReceiptJobRequest(BaseModel):
    image_id: str


@router.post("/receipt-jobs", status_code=status.HTTP_202_ACCEPTED)
async def create_receipt_job(
    body: ReceiptJobRequest, http_request: Request, session: DbSession, user_id: CurrentUser
) -> dict[str, dict[str, str]]:
    cloudbase = _cloudbase(http_request)
    if cloudbase is not None:
        job_id = str(uuid4())
        try:
            result = await cloudbase.rpc("tsb_create_receipt_job", {"p_job_id": job_id, "p_image_id": body.image_id, "p_owner_id": user_id})
        except (CloudBasePgConfigurationError, CloudBasePgUnavailable) as exc:
            raise _cloudbase_error(exc) from exc
        if not isinstance(result, dict) or result.get("not_found"):
            raise _not_found()
        return {"data": {"id": str(result["id"]), "status": str(result["status"])}}
    image = session.get(ReceiptImage, body.image_id)
    if image is None or image.owner_id != user_id or image.status != "uploaded":
        raise _not_found()
    job = ReceiptJob(image_id=image.id)
    session.add(job)
    session.commit()
    return {"data": {"id": job.id, "status": job.status}}


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
