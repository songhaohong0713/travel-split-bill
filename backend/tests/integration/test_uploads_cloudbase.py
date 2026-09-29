from collections.abc import Generator

import pytest
from app.api.v1.auth import create_access_token
from app.main import app
from fastapi.testclient import TestClient


class FakeCloudBasePg:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def rpc(self, name: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((name, payload))
        if name == "tsb_create_receipt_image":
            return {"id": payload["p_image_id"], "object_key": payload["p_object_key"], "status": "pending"}
        if name == "tsb_mark_receipt_uploaded":
            return {"id": payload["p_image_id"], "status": "uploaded"}
        return {"id": payload["p_job_id"], "status": "queued"}

    async def request(self, method: str, path: str, **kwargs: object) -> list[object]:
        self.calls.append((f"{method} {path}", kwargs))
        return []


class FakeReceiptAi:
    async def recognize(self, _: bytes) -> list[dict[str, str]]:
        return [{"source_text": "tea", "translated_text": "茶", "amount": "3.50", "currency": "USD"}]


@pytest.fixture
def client() -> Generator[tuple[TestClient, FakeCloudBasePg], None, None]:
    provider = FakeCloudBasePg()
    app.state.cloudbase_pg = provider
    app.state.receipt_ai = FakeReceiptAi()
    with TestClient(app) as test_client:
        yield test_client, provider
    delattr(app.state, "cloudbase_pg")
    delattr(app.state, "receipt_ai")


def test_receipt_job_uses_cloudbase_rpcs_and_persists_candidates(client: tuple[TestClient, FakeCloudBasePg]) -> None:
    test_client, provider = client
    headers = {"Authorization": f"Bearer {create_access_token('owner-1')}"}
    response = test_client.post(
        "/v1/receipt-jobs",
        headers=headers,
        data={"trip_id": "trip-1"},
        files={"file": ("receipt.jpg", b"jpeg", "image/jpeg")},
    )
    assert response.status_code == 202
    assert response.json()["data"]["status"] == "queued"
    assert response.json()["data"]["candidates"] == []
    assert [name for name, _ in provider.calls[:3]] == ["tsb_create_receipt_image", "tsb_mark_receipt_uploaded", "tsb_create_receipt_job"]
    processing_method, processing = provider.calls[3]
    completed_method, completed = provider.calls[4]
    assert processing_method == completed_method == "PATCH /receipt_jobs"
    assert processing["payload"]["status"] == "processing"
    assert completed["payload"]["status"] == "needs_review"
