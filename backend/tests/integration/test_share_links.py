from collections.abc import Generator

import pytest
from app.api.v1.auth import create_access_token
from app.db.session import configure_database, create_schema, drop_schema
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path) -> Generator[TestClient, None, None]:
    configure_database(f"sqlite+pysqlite:///{tmp_path / 'share.db'}")
    create_schema()
    with TestClient(app) as test_client:
        yield test_client
    drop_schema()


def test_public_share_reads_latest_published_projection_without_private_fields(client: TestClient) -> None:
    headers = {"Authorization": f"Bearer {create_access_token('owner')}"}
    trip = client.post('/v1/trips', headers=headers, json={'name': '东京', 'default_currency': 'CNY'}).json()['data']
    payload = {'participants': ['owner', 'friend'], 'settlement_currency': 'CNY', 'expenses': [{'expense_id': 'tea', 'payer_id': 'owner', 'items': [{'item_id': 'tea', 'amount': {'currency': 'JPY', 'amount': '1000'}, 'allocation': {'friend': '1'}}], 'actual_payment': {'currency': 'CNY', 'amount': '48.50'}}]}
    client.post(f"/v1/trips/{trip['id']}/settlements/publish", headers=headers, json=payload)

    created = client.post(f"/v1/trips/{trip['id']}/share-links", headers=headers, json={'expires_in_days': 7})
    public = client.get(f"/public/share/{created.json()['data']['token']}")

    assert public.status_code == 200
    assert public.json()['data']['transfers'][0]['amount']['amount'] == '48.50'
    assert 'owner_id' not in str(public.json())