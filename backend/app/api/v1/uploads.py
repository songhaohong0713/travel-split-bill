import logging
from io import BytesIO
from typing import Annotated, Any, Protocol, cast
from uuid import uuid4

import httpx
from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from PIL import Image, UnidentifiedImageError

from app.api.dependencies import CurrentUser, DbSession
from app.api.v1.trips import _not_found
from app.core.receipt_files import discard_failed_upload, image_for_job, storage_error
from app.db.models import ReceiptImage, ReceiptJob
from app.providers.cloudbase_pg import (
    CloudBasePgClient,
    CloudBasePgConfigurationError,
    CloudBasePgRequestError,
    CloudBasePgUnavailable,
)
from app.providers.deepseek_receipt_ai import DeepSeekReceiptAiError
from app.providers.receipt_storage import ReceiptStorageError

router = APIRouter(prefix="/v1", tags=["uploads"])
logger = logging.getLogger(__name__)


class ReceiptRecognizer(Protocol):
    async def recognize(self, image: bytes) -> list[dict[str, str]]: ...


def _cloudbase(request: Request) -> CloudBasePgClient | None:
    return getattr(request.app.state, "cloudbase_pg", None)


def _cloudbase_error(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": "DATABASE_UNAVAILABLE",
            "message": "CloudBase PostgreSQL is unavailable",
        },
    )


@router.post("/receipt-jobs", status_code=status.HTTP_202_ACCEPTED)
async def create_receipt_job(
    trip_id: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    http_request: Request,
    background_tasks: BackgroundTasks,
    session: DbSession,
    user_id: CurrentUser,
    recognize: Annotated[bool, Form()] = True,
) -> dict[str, object]:
    if file.content_type not in {"image/jpeg", "image/png", "application/octet-stream"}:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_RECEIPT_IMAGE",
                "message": "请选择 JPEG 或 PNG 小票图片",
            },
        )
    image_bytes = await file.read(5_000_001)
    if not image_bytes or len(image_bytes) > 5_000_000:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_RECEIPT_IMAGE",
                "message": "小票图片必须小于 5 MB",
            },
        )
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            if (
                image.format not in {"JPEG", "PNG"}
                or image.width * image.height > 40_000_000
            ):
                raise ValueError("invalid receipt image")
            image.load()
            content_type = "image/png" if image.format == "PNG" else "image/jpeg"
            ocr_bytes = image_bytes
            if image.format == "PNG" and recognize:
                # Keep original PNG bytes in storage; the existing OCR provider accepts JPEG.
                rgba = image.convert("RGBA")
                copy = Image.new("RGB", image.size, "white")
                copy.paste(rgba, mask=rgba.getchannel("A"))
                buffer = BytesIO()
                copy.save(buffer, format="JPEG", quality=95)
                ocr_bytes = buffer.getvalue()
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
    ) as exc:
        raise HTTPException(
            400,
            detail={
                "code": "INVALID_RECEIPT_IMAGE",
                "message": "请上传有效的 JPEG 或 PNG 小票图片",
            },
        ) from exc
    storage = getattr(http_request.app.state, "receipt_storage", None)
    if storage is None:
        raise storage_error()
    image_id, job_id = str(uuid4()), str(uuid4())
    extension = "png" if content_type == "image/png" else "jpg"
    key = f"{user_id}/{trip_id}/{image_id}.{extension}"
    cloudbase = _cloudbase(http_request)
    uploaded = False
    if cloudbase is not None:
        try:
            image_result = await cloudbase.rpc(
                "tsb_create_receipt_image",
                {
                    "p_image_id": image_id,
                    "p_owner_id": user_id,
                    "p_trip_id": trip_id,
                    "p_object_key": key,
                },
            )
            if not isinstance(image_result, dict) or image_result.get("not_found"):
                raise _not_found()
            await storage.upload(key, image_bytes, content_type)
            uploaded = True
            await cloudbase.request(
                "PATCH",
                "/receipt_images",
                params={"id": f"eq.{image_id}", "owner_id": f"eq.{user_id}"},
                payload={"status": "stored"},
            )
            result = await cloudbase.rpc(
                "tsb_create_receipt_job",
                {"p_job_id": job_id, "p_image_id": image_id, "p_owner_id": user_id},
            )
            if not isinstance(result, dict) or result.get("not_found"):
                raise _not_found()
        except HTTPException:
            if uploaded:
                await discard_failed_upload(storage, key, cloudbase)
            raise
        except ReceiptStorageError as exc:
            raise storage_error() from exc
        except (
            CloudBasePgConfigurationError,
            CloudBasePgRequestError,
            CloudBasePgUnavailable,
        ) as exc:
            if uploaded:
                await discard_failed_upload(storage, key, cloudbase)
            raise _cloudbase_error(exc) from exc
    else:
        from app.api.v1.trips import require_trip_member

        require_trip_member(session, trip_id, user_id)
        try:
            await storage.upload(key, image_bytes, content_type)
        except ReceiptStorageError as exc:
            raise storage_error() from exc
        session.add(
            ReceiptImage(
                id=image_id,
                owner_id=user_id,
                trip_id=trip_id,
                object_key=key,
                status="stored",
            )
        )
        session.add(ReceiptJob(id=job_id, image_id=image_id))
        try:
            session.commit()
        except Exception:
            session.rollback()
            await storage.delete(key)
            raise

    provider = getattr(http_request.app.state, "receipt_ai", None)
    if recognize:
        background_tasks.add_task(
            _complete_receipt_job, cloudbase, session, provider, job_id, ocr_bytes
        )
    else:
        await _persist_receipt_job(cloudbase, session, job_id, "completed", 0, [], None)
    logger.info("receipt_job queued job_id=%s image_bytes=%s", job_id, len(image_bytes))
    return {"data": {"id": job_id, "status": "queued", "candidates": []}}


async def _expense_receipt_image(trip_id, expense_id, request, session, user_id):
    from sqlalchemy import select

    from app.api.v1.trips import require_trip_member
    from app.db.models import ExpenseRecord

    cloud = _cloudbase(request)
    if cloud:
        member = await cloud.request(
            "GET",
            "/trip_members",
            params={
                "trip_id": f"eq.{trip_id}",
                "user_id": f"eq.{user_id}",
                "select": "id",
                "limit": "1",
            },
        )
        if not member:
            raise _not_found()
        records = await cloud.request(
            "GET",
            "/expenses",
            params={
                "id": f"eq.{expense_id}",
                "trip_id": f"eq.{trip_id}",
                "select": "payload_json",
                "limit": "1",
            },
        )
        if (
            not isinstance(records, list)
            or not records
            or not isinstance(records[0], dict)
        ):
            raise _not_found()
        payload = records[0]["payload_json"]
    else:
        require_trip_member(session, trip_id, user_id)
        record = session.scalar(
            select(ExpenseRecord).where(
                ExpenseRecord.id == expense_id, ExpenseRecord.trip_id == trip_id
            )
        )
        if record is None:
            raise _not_found()
        payload = record.payload_json
    job = payload.get("receipt_job_id")
    if not job:
        return None, "none"
    try:
        image = await image_for_job(trip_id, job, cloud, session)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        return None, "missing"
    return image, "available"


@router.get("/trips/{trip_id}/expenses/{expense_id}/receipt")
async def get_expense_receipt(
    trip_id: str,
    expense_id: str,
    request: Request,
    response: Response,
    session: DbSession,
    user_id: CurrentUser,
):
    response.headers["Cache-Control"] = "no-store"
    try:
        image, state = await _expense_receipt_image(
            trip_id, expense_id, request, session, user_id
        )
        if image is None:
            return {"data": {"status": state}}
        storage = getattr(request.app.state, "receipt_storage", None)
        if storage is None:
            raise storage_error()
        if not await storage.exists(image["object_key"]):
            return {"data": {"status": "missing"}}
        return {
            "data": {
                "status": "available",
                "url": await storage.signed_url(image["object_key"]),
                "expires_in": 300,
            }
        }
    except ReceiptStorageError as exc:
        raise storage_error() from exc
    except (
        CloudBasePgConfigurationError,
        CloudBasePgRequestError,
        CloudBasePgUnavailable,
    ) as exc:
        raise _cloudbase_error(exc) from exc


def _missing_receipt():
    return HTTPException(
        404,
        detail={
            "code": "RECEIPT_IMAGE_MISSING",
            "message": "原图不存在，请重新上传小票",
        },
    )


@router.get("/trips/{trip_id}/expenses/{expense_id}/receipt/file")
async def download_expense_receipt(
    trip_id: str,
    expense_id: str,
    request: Request,
    session: DbSession,
    user_id: CurrentUser,
):
    try:
        image, _ = await _expense_receipt_image(
            trip_id, expense_id, request, session, user_id
        )
        if image is None:
            raise _missing_receipt()
        storage = getattr(request.app.state, "receipt_storage", None)
        if storage is None:
            raise storage_error()
        content, content_type = await storage.download(image["object_key"])
        return Response(
            content=content,
            media_type=content_type,
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )
    except FileNotFoundError as exc:
        raise _missing_receipt() from exc
    except ReceiptStorageError as exc:
        raise storage_error() from exc
    except (
        CloudBasePgConfigurationError,
        CloudBasePgRequestError,
        CloudBasePgUnavailable,
    ) as exc:
        raise _cloudbase_error(exc) from exc


async def _complete_receipt_job(
    cloudbase: CloudBasePgClient | None,
    session: DbSession,
    provider: ReceiptRecognizer | None,
    job_id: str,
    image_bytes: bytes,
) -> None:
    await _persist_receipt_job(cloudbase, session, job_id, "processing", 0, [], None)
    try:
        status_value, attempts, candidates, error_code = await _recognize(
            provider, image_bytes
        )
    except Exception:
        logger.exception("receipt_job recognition_crashed job_id=%s", job_id)
        status_value, attempts, candidates, error_code = "failed", 0, [], "OCR_FAILED"
    logger.info(
        "receipt_job recognition_finished job_id=%s status=%s attempts=%s candidates=%s",
        job_id,
        status_value,
        attempts,
        len(candidates),
    )
    await _persist_receipt_job(
        cloudbase, session, job_id, status_value, attempts, candidates, error_code
    )


async def _persist_receipt_job(
    cloudbase: CloudBasePgClient | None,
    session: DbSession,
    job_id: str,
    status_value: str,
    attempts: int,
    candidates: list[dict[str, str]],
    error_code: str | None,
) -> None:
    if cloudbase is not None:
        try:
            await cloudbase.request(
                "PATCH",
                "/receipt_jobs",
                params={"id": f"eq.{job_id}"},
                payload={
                    "status": status_value,
                    "attempts": attempts,
                    "candidates_json": candidates,
                    "error_code": error_code,
                },
            )
        except (
            CloudBasePgConfigurationError,
            CloudBasePgRequestError,
            CloudBasePgUnavailable,
        ):
            logger.exception(
                "receipt_job status_persist_failed job_id=%s status=%s",
                job_id,
                status_value,
            )
    else:
        job = session.get(ReceiptJob, job_id)
        if job is None:
            logger.error("receipt_job status_persist_missing job_id=%s", job_id)
            return
        job.status, job.attempts, job.candidates_json, job.error_code = (
            status_value,
            attempts,
            candidates,
            error_code,
        )
        session.commit()


async def _recognize(
    provider: ReceiptRecognizer | None, image_bytes: bytes
) -> tuple[str, int, list[dict[str, str]], str | None]:
    if provider is None:
        return "failed", 0, [], "OCR_FAILED"
    timed_out = False
    for attempts in range(1, 3):
        try:
            candidates = await provider.recognize(image_bytes)
            return "needs_review", attempts, candidates, None
        except DeepSeekReceiptAiError as exc:
            timed_out = timed_out or isinstance(exc.__cause__, httpx.TimeoutException)
            logger.warning(
                "receipt_job recognition_attempt_failed attempt=%s error_type=%s",
                attempts,
                type(exc).__name__,
            )
            continue
    return "failed", 2, [], "OCR_TIMEOUT" if timed_out else "OCR_FAILED"


@router.get("/receipt-jobs/{job_id}")
async def get_receipt_job(
    job_id: str, http_request: Request, session: DbSession, user_id: CurrentUser
) -> dict[str, object]:
    cloudbase = _cloudbase(http_request)
    if cloudbase is not None:
        try:
            rows = await cloudbase.request(
                "GET",
                "/receipt_jobs",
                params={
                    "id": f"eq.{job_id}",
                    "select": "id,image_id,status,attempts,candidates_json,error_code",
                    "limit": "1",
                },
            )
            if not isinstance(rows, list) or not rows:
                raise _not_found()
            job_row = cast(dict[str, Any], rows[0])
            images = await cloudbase.request(
                "GET",
                "/receipt_images",
                params={
                    "id": f"eq.{job_row['image_id']}",
                    "owner_id": f"eq.{user_id}",
                    "select": "id",
                    "limit": "1",
                },
            )
        except (CloudBasePgConfigurationError, CloudBasePgUnavailable) as exc:
            raise _cloudbase_error(exc) from exc
        if not isinstance(images, list) or not images:
            raise _not_found()
        return {
            "data": {
                "id": str(job_row["id"]),
                "status": str(job_row["status"]),
                "attempts": int(job_row["attempts"]),
                "candidates": job_row.get("candidates_json") or [],
                "error_code": job_row.get("error_code"),
            }
        }
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
