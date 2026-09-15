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


@pytest.fixture
def client() -> Generator[tuple[TestClient, FakeCloudBasePg], None, None]:
    provider = FakeCloudBasePg()
    app.state.cloudbase_pg = provider
    with TestClient(app) as test_client:
        yield test_client, provider
    delattr(app.state, "cloudbase_pg")


def test_upload_lifecycle_uses_cloudbase_rpcs(client: tuple[TestClient, FakeCloudBasePg]) -> None:
    test_client, provider = client
    headers = {"Authorization": f"Bearer {create_access_token('owner-1')}"}
    upload = test_client.post("/v1/uploads", headers=headers, json={"trip_id": "trip-1", "mime_type": "image/jpeg", "byte_size": 1, "sha256": "a" * 64})
    assert upload.status_code == 201
    image_id = upload.json()["data"]["image_id"]
    assert test_client.post(f"/v1/uploads/{image_id}/complete", headers=headers).status_code == 200
    assert test_client.post("/v1/receipt-jobs", headers=headers, json={"image_id": image_id}).status_code == 202
    assert [name for name, _ in provider.calls] == ["tsb_create_receipt_image", "tsb_mark_receipt_uploaded", "tsb_create_receipt_job"]
