from collections.abc import Generator

import pytest
from app.api.v1.auth import create_access_token
from app.db.session import configure_database, create_schema, drop_schema
from app.main import app
from fastapi.testclient import TestClient


class FakeReceiptAi:
    async def recognize(self, _: bytes) -> list[dict[str, str]]:
        return [{"source_text": "お茶", "translated_text": "茶", "amount": "120", "currency": "JPY"}]


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
    assert response.json()['data']['status'] == 'needs_review'
    assert response.json()['data']['candidates'] == [
        {'source_text': 'お茶', 'translated_text': '茶', 'amount': '120', 'currency': 'JPY'}
    ]
    assert client.get(f"/v1/trips/{trip['id']}/expenses", headers=headers).json()['data'] == []


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
