from collections.abc import Generator

import pytest
from app.api.v1.auth import create_access_token
from app.db.session import configure_database, create_schema, drop_schema
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path) -> Generator[TestClient, None, None]:
    configure_database(f"sqlite+pysqlite:///{tmp_path / 'preview.db'}")
    create_schema()
    with TestClient(app) as test_client:
        yield test_client
    drop_schema()


def test_owner_can_preview_actual_payment_settlement(client: TestClient) -> None:
    headers = {"Authorization": f"Bearer {create_access_token('owner')}"}
    trip = client.post('/v1/trips', headers=headers, json={'name': '东京', 'default_currency': 'CNY'}).json()['data']
    payload = {
        'participants': ['owner', 'friend'],
        'settlement_currency': 'CNY',
        'expenses': [{
            'expense_id': 'matcha',
            'payer_id': 'owner',
            'items': [{'item_id': 'tea', 'amount': {'currency': 'JPY', 'amount': '1000'}, 'allocation': {'friend': '1'}}],
            'actual_payment': {'currency': 'CNY', 'amount': '48.50'},
        }],
    }

    response = client.post(f"/v1/trips/{trip['id']}/settlements/preview", headers=headers, json=payload)

    assert response.status_code == 200
    assert response.json()['data']['transfers'] == [
        {'from_participant_id': 'friend', 'to_participant_id': 'owner', 'amount': {'currency': 'CNY', 'amount': '48.50'}}
    ]