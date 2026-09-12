from uuid import uuid4

from fastapi import APIRouter, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.dependencies import CurrentUser, DbSession
from app.api.v1.trips import _not_found
from app.db.models import ReceiptImage, ReceiptJob, Trip

router = APIRouter(prefix="/v1", tags=["uploads"])


class UploadRequest(BaseModel):
    trip_id: str
    mime_type: str
    byte_size: int = Field(gt=0, le=10_000_000)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


@router.post("/uploads", status_code=status.HTTP_201_CREATED)
def create_upload(
    request: UploadRequest, session: DbSession, user_id: CurrentUser
) -> dict[str, dict[str, str]]:
    trip = session.scalar(
        select(Trip).where(Trip.id == request.trip_id, Trip.owner_id == user_id)
    )
    if trip is None:
        raise _not_found()
    if request.mime_type != "image/jpeg":
        from fastapi import HTTPException

        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_RECEIPT_IMAGE", "message": "仅支持 JPEG 小票"},
        )
    image_id = str(uuid4())
    key = f"receipts/{user_id}/{request.trip_id}/{image_id}.jpg"
    session.add(
        ReceiptImage(
            id=image_id, owner_id=user_id, trip_id=request.trip_id, object_key=key
        )
    )
    session.commit()
    return {
        "data": {
            "image_id": image_id,
            "object_key": key,
            "upload_url": f"/storage-upload/{key}",
        }
    }


@router.post("/uploads/{image_id}/complete")
def complete_upload(
    image_id: str, session: DbSession, user_id: CurrentUser
) -> dict[str, dict[str, str]]:
    image = session.get(ReceiptImage, image_id)
    if image is None or image.owner_id != user_id:
        raise _not_found()
    image.status = "uploaded"
    session.commit()
    return {"data": {"image_id": image.id, "status": image.status}}


class ReceiptJobRequest(BaseModel):
    image_id: str


@router.post("/receipt-jobs", status_code=status.HTTP_202_ACCEPTED)
def create_receipt_job(
    request: ReceiptJobRequest, session: DbSession, user_id: CurrentUser
) -> dict[str, dict[str, str]]:
    image = session.get(ReceiptImage, request.image_id)
    if image is None or image.owner_id != user_id or image.status != "uploaded":
        raise _not_found()
    job = ReceiptJob(image_id=image.id)
    session.add(job)
    session.commit()
    return {"data": {"id": job.id, "status": job.status}}


@router.get("/receipt-jobs/{job_id}")
def get_receipt_job(
    job_id: str, session: DbSession, user_id: CurrentUser
) -> dict[str, object]:
    job = session.get(ReceiptJob, job_id)
    if job is None:
        raise _not_found()
    image = session.get(ReceiptImage, job.image_id)
    if image is None or image.owner_id != user_id:
        raise _not_found()
    return {
        "data": {
            "id": job.id,
            "status": job.status,
            "attempts": job.attempts,
            "candidates": job.candidates_json or [],
            "error_code": job.error_code,
        }
    }
