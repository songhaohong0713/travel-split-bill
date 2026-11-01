from io import BytesIO
from uuid import uuid4

import pytest
from app.core.receipt_files import drain_cleanup
from app.db import session as db
from app.db.models import ReceiptCleanup, ReceiptImage, ReceiptJob
from app.main import app
from app.providers.receipt_storage import ReceiptStorageError
from PIL import Image
from sqlalchemy import select
from test_ledger import auth, expense, pay, preview
from test_ledger import ledger as ledger  # noqa: PLC0414


class MemoryStorage:
    def __init__(self):
        self.objects = {}
        self.fail = False

    async def upload(self, key, image, content_type="image/jpeg"):
        if self.fail:
            raise ReceiptStorageError("simulated storage outage")
        self.objects[key] = image

    async def exists(self, key):
        return key in self.objects

    async def signed_url(self, key):
        return "https://storage.example/" + key + "?short-lived"

    async def download(self, key):
        if key not in self.objects:
            raise FileNotFoundError("missing image")
        return self.objects[key], "image/png" if key.endswith(".png") else "image/jpeg"

    async def delete(self, key):
        if self.fail:
            raise ReceiptStorageError("simulated storage outage")
        self.objects.pop(key, None)


@pytest.fixture
def originals(ledger):
    store = MemoryStorage()
    app.state.receipt_storage = store
    try:
        yield *ledger, store
    finally:
        delattr(app.state, "receipt_storage")


def jpeg():
    out = BytesIO()
    Image.new("RGB", (2, 2), "white").save(out, format="JPEG")
    return out.getvalue()


def upload(c, base, *, recognize=False):
    response = c.post(
        "/v1/receipt-jobs",
        headers=auth(),
        data={"trip_id": base.rsplit("/", 1)[-1], "recognize": str(recognize).lower()},
        files={"file": ("receipt.jpg", jpeg(), "image/jpeg")},
    )
    assert response.status_code == 202, response.text
    return response.json()["data"]["id"]


def attach(c, base, saved, body, job):
    body["payload"]["receipt_job_id"] = job
    r = c.patch(
        base + "/expenses/" + saved["id"],
        headers=auth(),
        json={**body, "revision": saved["revision"]},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_original_persists_after_save_and_both_members_can_view_but_outsiders_cannot(
    originals,
):
    c, base, store = originals
    job = upload(c, base)
    assert list(store.objects.values()) == [jpeg()]
    saved, body = expense(c, base)
    attach(c, base, saved, body, job)
    endpoint = base + "/expenses/" + saved["id"] + "/receipt"
    for user in ["a", "b"]:
        r = c.get(endpoint, headers=auth(user))
        assert r.status_code == 200
        assert r.json()["data"]["status"] == "available"
        assert r.json()["data"]["url"].endswith("?short-lived")
        assert r.headers["cache-control"] == "no-store"
    assert c.get(endpoint, headers=auth("outsider")).status_code == 404
    assert c.get(endpoint).status_code == 401


def test_upload_failure_never_reports_stored_or_starts_ocr(originals):
    c, base, store = originals
    store.fail = True
    r = c.post(
        "/v1/receipt-jobs",
        headers=auth(),
        data={"trip_id": base.rsplit("/", 1)[-1]},
        files={"file": ("receipt.jpg", jpeg(), "image/jpeg")},
    )
    assert r.status_code == 503
    with db.SessionLocal() as s:
        assert not s.scalars(select(ReceiptJob)).all()
        assert not s.scalars(
            select(ReceiptImage).where(ReceiptImage.status == "stored")
        ).all()


def test_original_remains_available_when_recognition_fails(originals):
    c, base, store = originals
    app.state.receipt_ai = None
    job = upload(c, base, recognize=True)
    assert (
        c.get("/v1/receipt-jobs/" + job, headers=auth()).json()["data"]["status"]
        == "failed"
    )
    saved, body = expense(c, base)
    attach(c, base, saved, body, job)
    assert (
        c.get(base + "/expenses/" + saved["id"] + "/receipt", headers=auth()).json()[
            "data"
        ]["status"]
        == "available"
    )
    assert len(store.objects) == 1


def test_old_missing_original_and_non_receipt_expenses_are_distinct(originals):
    c, base, _ = originals
    saved, body = expense(c, base)
    endpoint = base + "/expenses/" + saved["id"] + "/receipt"
    assert c.get(endpoint, headers=auth()).json()["data"]["status"] == "none"
    job = str(uuid4())
    with db.SessionLocal() as s:
        s.add(
            ReceiptImage(
                id="old-image",
                owner_id="a",
                trip_id=base.rsplit("/", 1)[-1],
                object_key="old/nonexistent.jpg",
                status="uploaded",
            )
        )
        s.flush()
        s.add(ReceiptJob(id=job, image_id="old-image", status="needs_review"))
        s.commit()
    attach(c, base, saved, body, job)
    assert c.get(endpoint, headers=auth("b")).json()["data"]["status"] == "missing"


def test_cross_trip_reference_is_rejected_even_when_user_belongs_to_both(originals):
    c, base, _ = originals
    job = upload(c, base)
    other = c.post(
        "/v1/trips",
        headers=auth(),
        json={"name": "另一段旅行", "default_currency": "CNY"},
    ).json()["data"]["id"]
    _, body = expense(c, base)
    body["payload"]["participants"] = ["a"]
    body["payload"]["expenses"][0]["items"][0]["allocation"] = {"a": "1"}
    body["payload"]["receipt_job_id"] = job
    r = c.post(
        "/v1/trips/" + other + "/expenses",
        headers={**auth(), "Idempotency-Key": str(uuid4())},
        json=body,
    )
    assert r.status_code == 404


def test_replace_original_on_settled_day_does_not_change_money_and_shared_file_is_retained(
    originals,
):
    c, base, store = originals
    saved, body = expense(c, base)
    assert pay(c, base, preview(c, base)).status_code == 201
    before = c.get(base + "/ledger", headers=auth()).json()["data"]
    first = upload(c, base)
    saved = attach(c, base, saved, body, first)
    second, other_body = expense(c, base, day="2026-10-06")
    attach(c, base, second, other_body, first)
    saved = attach(c, base, saved, body, upload(c, base))
    after = c.get(base + "/ledger", headers=auth()).json()["data"]
    day = next(d for d in after["days"] if d["date"] == "2026-10-07")
    assert day["groups"] == before["days"][0]["groups"]
    assert day["status"] == "settled"
    assert not day["carry_entries"]
    assert len(store.objects) == 2
    assert (
        c.delete(base + "/expenses/" + saved["id"], headers=auth()).status_code == 204
    )
    assert len(store.objects) == 1
    assert (
        c.get(
            base + "/expenses/" + second["id"] + "/receipt", headers=auth("b")
        ).json()["data"]["status"]
        == "available"
    )
    assert c.delete(base, headers=auth()).status_code == 204
    assert not store.objects


def test_fake_jpeg_is_rejected_without_storing_bytes(originals):
    c, base, store = originals
    r = c.post(
        "/v1/receipt-jobs",
        headers=auth(),
        data={"trip_id": base.rsplit("/", 1)[-1]},
        files={"file": ("fake.jpg", b"not-a-jpeg", "image/jpeg")},
    )
    assert r.status_code == 400
    assert not store.objects


def test_truncated_jpeg_is_rejected_before_upload(originals):
    c, base, store = originals
    r = c.post(
        "/v1/receipt-jobs",
        headers=auth(),
        data={"trip_id": base.rsplit("/", 1)[-1]},
        files={"file": ("receipt.jpg", jpeg()[:-2], "image/jpeg")},
    )
    assert r.status_code == 400
    assert not store.objects


def test_failed_deletion_survives_new_db_session_and_can_be_retried(originals):
    import asyncio

    c, base, store = originals
    saved, body = expense(c, base)
    attach(c, base, saved, body, upload(c, base))
    store.fail = True
    assert c.delete(base, headers=auth()).status_code == 204
    assert len(store.objects) == 1
    with db.SessionLocal() as s:
        assert len(s.scalars(select(ReceiptCleanup)).all()) == 1
    store.fail = False
    with db.SessionLocal() as s:
        asyncio.run(drain_cleanup(None, s, store))
        assert s.scalars(select(ReceiptCleanup)).all() == []
    assert not store.objects


@pytest.mark.parametrize(
    "content_type", ["image/jpeg", "image/png", "application/octet-stream"]
)
def test_png_original_is_preserved_while_ocr_receives_jpeg_even_with_mislabeled_mime(
    originals,
    content_type,
):
    c, base, store = originals
    out = BytesIO()
    Image.new("RGBA", (20, 20), (0, 0, 0, 0)).save(out, format="PNG")
    raw = out.getvalue()

    class CaptureRecognizer:
        async def recognize(self, image):
            with Image.open(BytesIO(image)) as decoded:
                assert decoded.format == "JPEG"
                assert decoded.convert("RGB").getpixel((0, 0)) == (255, 255, 255)
            return [{"source_text": "tea", "amount": "123", "currency": "JPY"}]

    app.state.receipt_ai = CaptureRecognizer()
    r = c.post(
        "/v1/receipt-jobs",
        headers=auth(),
        data={"trip_id": base.rsplit("/", 1)[-1]},
        files={"file": ("receipt.jpg", raw, content_type)},
    )
    assert r.status_code == 202, r.text
    assert list(store.objects.values()) == [raw]
    assert next(iter(store.objects)).endswith(".png")
    job = c.get("/v1/receipt-jobs/" + r.json()["data"]["id"], headers=auth()).json()[
        "data"
    ]
    assert job["status"] == "needs_review"
    assert job["candidates"][0]["amount"] == "123"


def test_original_file_endpoint_requires_membership_and_returns_original_bytes(
    originals,
):
    c, base, store = originals
    saved, body = expense(c, base)
    attach(c, base, saved, body, upload(c, base))
    endpoint = base + "/expenses/" + saved["id"] + "/receipt/file"
    for user in ["a", "b"]:
        r = c.get(endpoint, headers=auth(user))
        assert r.status_code == 200
        assert r.content == jpeg()
        assert r.headers["content-type"] == "image/jpeg"
        assert r.headers["cache-control"] == "no-store"
    assert c.get(endpoint, headers=auth("outsider")).status_code == 404
    assert c.get(endpoint).status_code == 401
    store.objects.clear()
    r = c.get(endpoint, headers=auth())
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "RECEIPT_IMAGE_MISSING"


def test_permanently_failed_cleanup_does_not_starve_later_files(ledger):
    import asyncio
    from datetime import UTC, datetime, timedelta

    class Storage:
        def __init__(self):
            self.deleted = []

        async def delete(self, key):
            if key.startswith("bad"):
                raise ReceiptStorageError("forbidden")
            self.deleted.append(key)

    store = Storage()
    with db.SessionLocal() as s:
        for i in range(21):
            s.add(
                ReceiptCleanup(
                    object_key=("bad" if i < 20 else "good") + str(i),
                    created_at=datetime.now(UTC) + timedelta(seconds=i),
                )
            )
        s.commit()
        asyncio.run(drain_cleanup(None, s, store))
        asyncio.run(drain_cleanup(None, s, store))
        assert store.deleted == ["good20"]
        assert len(s.scalars(select(ReceiptCleanup)).all()) == 20
