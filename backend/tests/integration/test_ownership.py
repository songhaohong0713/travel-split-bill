from collections.abc import Generator

import pytest
from app.api.v1.auth import create_access_token
from app.db.session import configure_database, create_schema, drop_schema
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path) -> Generator[TestClient, None, None]:
    configure_database(f"sqlite+pysqlite:///{tmp_path / 'ownership.db'}")
    create_schema()
    with TestClient(app) as test_client:
        yield test_client
    drop_schema()


def auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def create_trip(client: TestClient, user_id: str) -> str:
    response = client.post(
        "/v1/trips",
        headers=auth(user_id),
        json={"name": "东京", "default_currency": "CNY"},
    )
    assert response.status_code == 201
    return response.json()["data"]["id"]


def test_other_owner_cannot_read_expenses(client: TestClient) -> None:
    trip_id = create_trip(client, "owner-a")
    create_response = client.post(
        f"/v1/trips/{trip_id}/expenses",
        headers={
            **auth("owner-a"),
            "Idempotency-Key": "4d7ccd23-cd86-45f8-b11f-d522d0a5ece5",
        },
        json={"occurred_at": "2026-08-15", "payload": {"note": "抹茶"}},
    )
    assert create_response.status_code == 201

    response = client.get(f"/v1/trips/{trip_id}/expenses", headers=auth("owner-b"))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_same_idempotency_key_creates_one_expense(client: TestClient) -> None:
    trip_id = create_trip(client, "owner-a")
    headers = {
        **auth("owner-a"),
        "Idempotency-Key": "4d7ccd23-cd86-45f8-b11f-d522d0a5ece5",
    }
    request = {"occurred_at": "2026-08-15", "payload": {"note": "抹茶"}}

    first = client.post(f"/v1/trips/{trip_id}/expenses", headers=headers, json=request)
    second = client.post(f"/v1/trips/{trip_id}/expenses", headers=headers, json=request)
    listed = client.get(f"/v1/trips/{trip_id}/expenses", headers=auth("owner-a"))

    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert len(listed.json()["data"]) == 1
