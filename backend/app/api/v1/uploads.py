from uuid import uuid4

from fastapi import APIRouter, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.dependencies import CurrentUser, DbSession
from app.api.v1.trips import _not_found
from app.db.models import Trip

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
    return {
        "data": {
            "image_id": image_id,
            "object_key": key,
            "upload_url": f"/storage-upload/{key}",
        }
    }
