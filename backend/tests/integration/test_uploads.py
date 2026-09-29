import logging
from collections.abc import Generator

import httpx
import pytest
from app.api.v1.auth import create_access_token
from app.api.v1.uploads import _recognize
from app.db.session import configure_database, create_schema, drop_schema
from app.main import app
from app.providers.deepseek_receipt_ai import DeepSeekReceiptAiError
from fastapi.testclient import TestClient


class FakeReceiptAi:
    async def recognize(self, _: bytes) -> list[dict[str, str]]:
        return [{"source_text": "お茶", "translated_text": "茶", "amount": "120", "currency": "JPY"}]


class TimeoutReceiptAi:
    async def recognize(self, _: bytes) -> list[dict[str, str]]:
        try:
            raise httpx.ReadTimeout("slow provider")
        except httpx.ReadTimeout as exc:
            raise DeepSeekReceiptAiError("DeepSeek request failed") from exc


@pytest.mark.anyio
async def test_recognition_marks_provider_timeouts_after_two_attempts() -> None:
    assert await _recognize(TimeoutReceiptAi(), b"jpeg") == ("failed", 2, [], "OCR_TIMEOUT")


@pytest.fixture
def client(tmp_path) -> Generator[TestClient, None, None]:
    configure_database(f"sqlite+pysqlite:///{tmp_path / 'uploads.db'}")
    create_schema()
    app.state.receipt_ai = FakeReceiptAi()
    with TestClient(app) as test_client:
        yield test_client
    delattr(app.state, "receipt_ai")
    drop_schema()


def test_receipt_job_processes_jpeg_without_creating_expense(client: TestClient) -> None:
    headers = {"Authorization": f"Bearer {create_access_token('owner')}"}
    trip = client.post('/v1/trips', headers=headers, json={'name': '东京', 'default_currency': 'JPY'}).json()['data']

    response = client.post(
        '/v1/receipt-jobs',
        headers=headers,
        data={'trip_id': trip['id']},
        files={'file': ('receipt.jpg', b'jpeg', 'image/jpeg')},
    )

    assert response.status_code == 202
    assert response.json()['data'] == {'id': response.json()['data']['id'], 'status': 'queued', 'candidates': []}
    job = client.get(f"/v1/receipt-jobs/{response.json()['data']['id']}", headers=headers)
    assert job.json()['data']['status'] == 'needs_review'
    assert job.json()['data']['candidates'] == [
        {'source_text': 'お茶', 'translated_text': '茶', 'amount': '120', 'currency': 'JPY'}
    ]
    assert client.get(f"/v1/trips/{trip['id']}/expenses", headers=headers).json()['data'] == []


def test_receipt_job_logs_safe_recognition_metadata(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    headers = {"Authorization": f"Bearer {create_access_token('owner')}"}
    trip = client.post("/v1/trips", headers=headers, json={"name": "东京", "default_currency": "JPY"}).json()["data"]

    client.post(
        "/v1/receipt-jobs",
        headers=headers,
        data={"trip_id": trip["id"]},
        files={"file": ("receipt.jpg", b"jpeg", "image/jpeg")},
    )

    assert "status=needs_review attempts=1 candidates=1" in caplog.text
    assert "お茶" not in caplog.text


def test_receipt_job_rejects_non_jpeg(client: TestClient) -> None:
    headers = {"Authorization": f"Bearer {create_access_token('owner')}"}
    trip = client.post('/v1/trips', headers=headers, json={'name': '东京', 'default_currency': 'JPY'}).json()['data']

    response = client.post(
        '/v1/receipt-jobs',
        headers=headers,
        data={'trip_id': trip['id']},
        files={'file': ('receipt.png', b'png', 'image/png')},
    )

    assert response.status_code == 400
