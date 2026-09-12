from collections.abc import Generator

import pytest
from app.api.v1.auth import create_access_token
from app.db.session import configure_database, create_schema, drop_schema
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path) -> Generator[TestClient, None, None]:
    configure_database(f"sqlite+pysqlite:///{tmp_path / 'publish.db'}")
    create_schema()
    with TestClient(app) as test_client:
        yield test_client
    drop_schema()


def test_publish_persists_latest_immutable_settlement_snapshot(client: TestClient) -> None:
    headers = {'Authorization': f'Bearer {create_access_token("owner")}', 'Idempotency-Key': 'e5c6954b-aabd-4edb-a8d1-064d21f42e9c'}
    trip = client.post('/v1/trips', headers=headers, json={'name': '东京', 'default_currency': 'CNY'}).json()['data']
    payload = {'participants': ['owner', 'friend'], 'settlement_currency': 'CNY', 'expenses': [{'expense_id': 'tea', 'payer_id': 'owner', 'items': [{'item_id': 'tea', 'amount': {'currency': 'JPY', 'amount': '1000'}, 'allocation': {'friend': '1'}}], 'actual_payment': {'currency': 'CNY', 'amount': '48.50'}}]}

    response = client.post(f"/v1/trips/{trip['id']}/settlements/publish", headers=headers, json=payload)

    assert response.status_code == 201
    published = response.json()['data']
    assert published['trip_id'] == trip['id']
    assert published['result']['transfers'][0]['amount']['amount'] == '48.50'
    assert 'owner_id' not in str(published)