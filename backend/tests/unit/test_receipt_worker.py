from app.db.models import ReceiptImage, ReceiptJob
from app.worker.receipt_processor import process_next_job


class FakeOcr:
    def recognize(self, image: bytes, language: str = "auto") -> list[str]:
        return ["お茶 120", "合計 120"]


class FailingOcr:
    def recognize(self, image: bytes, language: str = "auto") -> list[str]:
        raise RuntimeError("provider unavailable")


def test_worker_marks_successful_job_needs_review(session) -> None:
    session.add(
        ReceiptImage(
            id="image",
            owner_id="owner",
            trip_id="trip",
            object_key="key",
            status="uploaded",
        )
    )
    session.add(ReceiptJob(id="job", image_id="image", status="queued"))
    session.commit()
    job = process_next_job(session, FakeOcr(), lambda _: b"image")
    assert job is not None
    assert job.status == "needs_review"


def test_worker_marks_provider_failure_failed(session) -> None:
    session.add(
        ReceiptImage(
            id="image",
            owner_id="owner",
            trip_id="trip",
            object_key="key",
            status="uploaded",
        )
    )
    session.add(ReceiptJob(id="job", image_id="image", status="queued"))
    session.commit()
    job = process_next_job(session, FailingOcr(), lambda _: b"image")
    assert job is not None
    assert job.status == "failed"
