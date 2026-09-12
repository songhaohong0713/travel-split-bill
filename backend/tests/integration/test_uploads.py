from collections.abc import Generator

import pytest
from app.api.v1.auth import create_access_token
from app.db.session import configure_database, create_schema, drop_schema
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path) -> Generator[TestClient, None, None]:
    configure_database(f"sqlite+pysqlite:///{tmp_path / 'uploads.db'}")
    create_schema()
    with TestClient(app) as test_client:
        yield test_client
    drop_schema()


def test_owner_can_request_single_receipt_upload_url(client: TestClient) -> None:
    headers = {"Authorization": f"Bearer {create_access_token('owner')}"}
    trip = client.post('/v1/trips', headers=headers, json={'name': '东京', 'default_currency': 'CNY'}).json()['data']
    response = client.post('/v1/uploads', headers=headers, json={'trip_id': trip['id'], 'mime_type': 'image/jpeg', 'byte_size': 1234, 'sha256': 'a' * 64})
    assert response.status_code == 201
    assert response.json()['data']['upload_url']