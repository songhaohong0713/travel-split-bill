"""Receipt associations and durable object cleanup, independent of money calculations."""

import logging
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, select

from app.db.models import ExpenseRecord, ReceiptCleanup, ReceiptImage, ReceiptJob
from app.providers.cloudbase_pg import CloudBasePgError
from app.providers.receipt_storage import ReceiptStorageError

logger = logging.getLogger(__name__)


def storage_error():
    return HTTPException(
        503,
        detail={
            "code": "RECEIPT_STORAGE_UNAVAILABLE",
            "message": "小票图片暂时无法保存或读取，请重试",
        },
    )


async def discard_failed_upload(storage, key, cloud):
    try:
        await storage.delete(key)
    except ReceiptStorageError:
        # A concurrently deleted trip may have removed the reservation and its old queue item.
        try:
            await cloud.request("POST", "/receipt_cleanup", payload={"object_key": key})
        except CloudBasePgError:
            logger.warning("receipt_upload_cleanup_retry_pending")


async def image_for_job(trip_id, job_id, cloud, session):
    from app.api.v1.trips import _not_found

    try:
        UUID(str(job_id))
    except (ValueError, TypeError):
        raise _not_found() from None
    if cloud is not None:
        jobs = await cloud.request(
            "GET",
            "/receipt_jobs",
            params={"id": f"eq.{job_id}", "select": "image_id", "limit": "1"},
        )
        if not jobs:
            raise _not_found()
        images = await cloud.request(
            "GET",
            "/receipt_images",
            params={
                "id": f"eq.{jobs[0]['image_id']}",
                "trip_id": f"eq.{trip_id}",
                "select": "id,object_key,status",
                "limit": "1",
            },
        )
        if not images:
            raise _not_found()
        return images[0]
    job = session.get(ReceiptJob, job_id)
    image = session.get(ReceiptImage, job.image_id) if job else None
    if image is None or image.trip_id != trip_id:
        raise _not_found()
    return {"id": image.id, "object_key": image.object_key, "status": image.status}


async def validate_receipt_reference(trip_id, payload, cloud, session):
    if not payload.get("receipt_job_id"):
        return
    image = await image_for_job(trip_id, payload["receipt_job_id"], cloud, session)
    if image["status"] not in {"uploaded", "stored"}:
        raise HTTPException(
            409,
            detail={
                "code": "RECEIPT_NOT_READY",
                "message": "小票尚未保存成功，请重新上传",
            },
        )


def queue_image(session, image):
    if (
        image.status == "stored"
        and session.get(ReceiptCleanup, image.object_key) is None
    ):
        session.add(ReceiptCleanup(object_key=image.object_key))
    session.execute(delete(ReceiptJob).where(ReceiptJob.image_id == image.id))
    session.delete(image)


def cleanup_unreferenced(session, trip_id, previous_job):
    if not previous_job:
        return
    # Two-person trips have small ledgers. Scan only this trip, retaining shared attachments.
    records = session.scalars(
        select(ExpenseRecord).where(ExpenseRecord.trip_id == trip_id)
    ).all()
    if any(r.payload_json.get("receipt_job_id") == previous_job for r in records):
        return
    job = session.get(ReceiptJob, previous_job)
    image = session.get(ReceiptImage, job.image_id) if job else None
    if image and image.trip_id == trip_id:
        queue_image(session, image)


async def drain_cleanup(cloud, session, storage):
    """Bounded, idempotent worker. Failed deletions remain queued for the next run."""
    if storage is None:
        return
    try:
        rows = (
            await cloud.request(
                "GET",
                "/receipt_cleanup",
                params={
                    "select": "object_key,attempts",
                    "order": "attempts.asc,created_at.asc",
                    "limit": "20",
                },
            )
            if cloud
            else session.scalars(
                select(ReceiptCleanup)
                .order_by(ReceiptCleanup.attempts, ReceiptCleanup.created_at)
                .limit(20)
            ).all()
        )
        for row in rows:
            key = row["object_key"] if cloud else row.object_key
            try:
                await storage.delete(key)
                if cloud:
                    await cloud.request(
                        "DELETE", "/receipt_cleanup", params={"object_key": f"eq.{key}"}
                    )
                else:
                    session.delete(row)
                    session.commit()
            except (ReceiptStorageError, CloudBasePgError):
                try:
                    if cloud:
                        await cloud.request(
                            "PATCH",
                            "/receipt_cleanup",
                            params={"object_key": f"eq.{key}"},
                            payload={"attempts": row.get("attempts", 0) + 1},
                        )
                    else:
                        row.attempts += 1
                        session.commit()
                except CloudBasePgError:
                    logger.warning("receipt_cleanup_retry_update_failed")
                logger.warning("receipt_cleanup_retry_pending")
    except CloudBasePgError:
        logger.warning("receipt_cleanup_queue_unavailable")


async def cleanup_after_request(cloud, storage):
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        await drain_cleanup(cloud, session, storage)
