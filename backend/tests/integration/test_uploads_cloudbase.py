from collections.abc import Generator

import pytest
from app.api.v1.auth import create_access_token
from app.main import app
from fastapi.testclient import TestClient
from test_receipt_originals import MemoryStorage, jpeg


class FakeCloudBasePg:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def rpc(self, name: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((name, payload))
        if name == "tsb_create_receipt_image":
            return {
                "id": payload["p_image_id"],
                "object_key": payload["p_object_key"],
                "status": "pending",
            }
        if name == "tsb_mark_receipt_uploaded":
            return {"id": payload["p_image_id"], "status": "uploaded"}
        return {"id": payload["p_job_id"], "status": "queued"}

    async def request(self, method: str, path: str, **kwargs: object) -> list[object]:
        self.calls.append((f"{method} {path}", kwargs))
        return []


class FakeReceiptAi:
    async def recognize(self, _: bytes) -> list[dict[str, str]]:
        return [
            {
                "source_text": "tea",
                "translated_text": "茶",
                "amount": "3.50",
                "currency": "USD",
            }
        ]


@pytest.fixture
def client() -> Generator[tuple[TestClient, FakeCloudBasePg], None, None]:
    provider = FakeCloudBasePg()
    app.state.cloudbase_pg = provider
    app.state.receipt_ai = FakeReceiptAi()
    app.state.receipt_storage = MemoryStorage()
    with TestClient(app) as test_client:
        yield test_client, provider
    delattr(app.state, "cloudbase_pg")
    delattr(app.state, "receipt_ai")
    delattr(app.state, "receipt_storage")


def test_receipt_job_uses_cloudbase_rpcs_and_persists_candidates(
    client: tuple[TestClient, FakeCloudBasePg],
) -> None:
    test_client, provider = client
    headers = {"Authorization": f"Bearer {create_access_token('owner-1')}"}
    response = test_client.post(
        "/v1/receipt-jobs",
        headers=headers,
        data={"trip_id": "trip-1"},
        files={"file": ("receipt.jpg", jpeg(), "image/jpeg")},
    )
    assert response.status_code == 202
    assert response.json()["data"]["status"] == "queued"
    assert response.json()["data"]["candidates"] == []
    assert [name for name, _ in provider.calls[:3]] == [
        "tsb_create_receipt_image",
        "PATCH /receipt_images",
        "tsb_create_receipt_job",
    ]
    assert provider.calls[1][1]["payload"] == {"status": "stored"}
    processing_method, processing = provider.calls[3]
    completed_method, completed = provider.calls[4]
    assert processing_method == completed_method == "PATCH /receipt_jobs"
    assert processing["payload"]["status"] == "processing"
    assert completed["payload"]["status"] == "needs_review"


def test_trip_deleted_during_upload_compensates_file_even_when_reserved_row_is_gone(
    client,
):
    test_client, provider = client
    original = provider.rpc

    async def deleted_trip(name, payload):
        if name == "tsb_create_receipt_job":
            return {"not_found": True}
        return await original(name, payload)

    provider.rpc = deleted_trip
    response = test_client.post(
        "/v1/receipt-jobs",
        headers={"Authorization": f"Bearer {create_access_token('owner-1')}"},
        data={"trip_id": "trip-1"},
        files={"file": ("receipt.jpg", jpeg(), "image/jpeg")},
    )
    assert response.status_code == 404
    assert app.state.receipt_storage.objects == {}


def test_concurrent_trip_delete_requeues_compensation_if_storage_delete_fails(client):
    from app.providers.receipt_storage import ReceiptStorageError

    test_client, provider = client
    original = provider.rpc

    async def deleted_trip(name, payload):
        if name == "tsb_create_receipt_job":
            return {"not_found": True}
        return await original(name, payload)

    async def failed_delete(key):
        raise ReceiptStorageError("temporary outage")

    provider.rpc = deleted_trip
    app.state.receipt_storage.delete = failed_delete
    response = test_client.post(
        "/v1/receipt-jobs",
        headers={"Authorization": f"Bearer {create_access_token('owner-1')}"},
        data={"trip_id": "trip-1"},
        files={"file": ("receipt.jpg", jpeg(), "image/jpeg")},
    )
    assert response.status_code == 404
    queued = [
        data["payload"]["object_key"]
        for method, data in provider.calls
        if method == "POST /receipt_cleanup"
    ]
    assert queued == list(app.state.receipt_storage.objects)
