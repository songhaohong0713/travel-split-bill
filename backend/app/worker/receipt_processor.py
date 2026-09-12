from collections.abc import Callable
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ReceiptImage, ReceiptJob


class OcrProvider(Protocol):
    def recognize(self, image: bytes, language: str = "auto") -> list[str]: ...


def process_next_job(
    session: Session, provider: OcrProvider, load_image: Callable[[str], bytes]
) -> ReceiptJob | None:
    job = session.scalar(
        select(ReceiptJob).where(ReceiptJob.status == "queued").order_by(ReceiptJob.id)
    )
    if job is None:
        return None
    job.status = "processing"
    session.flush()
    image = session.get(ReceiptImage, job.image_id)
    try:
        if image is None:
            raise RuntimeError("image missing")
        provider.recognize(load_image(image.object_key))
        job.status = "needs_review"
    except (OSError, RuntimeError, ValueError):
        job.status = "failed"
    session.commit()
    return job
