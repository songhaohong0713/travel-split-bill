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


def test_trip_list_only_returns_current_owners_trips(client: TestClient) -> None:
    first_trip_id = create_trip(client, "owner-a")
    create_trip(client, "owner-b")

    response = client.get("/v1/trips", headers=auth("owner-a"))

    assert response.status_code == 200
    assert response.json()["data"] == [
        {
            "id": first_trip_id,
            "name": "东京",
            "default_currency": "CNY",
            "member_count": 1,
            "is_owner": True,
        }
    ]


def test_owner_can_update_expense_with_current_revision(client: TestClient) -> None:
    trip_id = create_trip(client, "owner-a")
    created = client.post(
        f"/v1/trips/{trip_id}/expenses",
        headers={
            **auth("owner-a"),
            "Idempotency-Key": "a9d5c121-faa7-4104-a455-ae9e1ca81c62",
        },
        json={"occurred_at": "2026-08-15", "payload": {"note": "抹茶"}},
    )
    expense_id = created.json()["data"]["id"]

    response = client.patch(
        f"/v1/trips/{trip_id}/expenses/{expense_id}",
        headers=auth("owner-a"),
        json={
            "revision": 1,
            "occurred_at": "2026-08-16",
            "payload": {"note": "抹茶拿铁"},
        },
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "id": expense_id,
        "revision": 2,
        "occurred_at": "2026-08-16",
        "payload": {"note": "抹茶拿铁"},
    }


def test_expense_update_rejects_stale_revision_and_other_owner(
    client: TestClient,
) -> None:
    trip_id = create_trip(client, "owner-a")
    created = client.post(
        f"/v1/trips/{trip_id}/expenses",
        headers={
            **auth("owner-a"),
            "Idempotency-Key": "bf2379ae-55a2-45a0-8147-34ccf27972c8",
        },
        json={"occurred_at": "2026-08-15", "payload": {"note": "抹茶"}},
    )
    expense_id = created.json()["data"]["id"]

    stale = client.patch(
        f"/v1/trips/{trip_id}/expenses/{expense_id}",
        headers=auth("owner-a"),
        json={
            "revision": 99,
            "occurred_at": "2026-08-15",
            "payload": {"note": "抹茶"},
        },
    )
    forbidden = client.patch(
        f"/v1/trips/{trip_id}/expenses/{expense_id}",
        headers=auth("owner-b"),
        json={
            "revision": 1,
            "occurred_at": "2026-08-15",
            "payload": {"note": "抹茶"},
        },
    )

    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "REVISION_CONFLICT"
    assert forbidden.status_code == 404
    assert forbidden.json()["error"]["code"] == "NOT_FOUND"


def test_only_trip_owner_can_delete_expenses_and_trip(client: TestClient) -> None:
    trip_id = create_trip(client, "owner-a")
    created = client.post(
        f"/v1/trips/{trip_id}/expenses",
        headers={
            **auth("owner-a"),
            "Idempotency-Key": "e3ea5a6b-809d-40aa-b9b8-5507f97bfca1",
        },
        json={"occurred_at": "2026-09-28", "payload": {"note": "晚餐"}},
    )
    expense_id = created.json()["data"]["id"]

    forbidden = client.delete(
        f"/v1/trips/{trip_id}/expenses/{expense_id}", headers=auth("owner-b")
    )
    deleted_expense = client.delete(
        f"/v1/trips/{trip_id}/expenses/{expense_id}", headers=auth("owner-a")
    )
    deleted_trip = client.delete(f"/v1/trips/{trip_id}", headers=auth("owner-a"))

    assert forbidden.status_code == 404
    assert deleted_expense.status_code == 204
    assert deleted_trip.status_code == 204
    assert client.get(f"/v1/trips/{trip_id}/expenses", headers=auth("owner-a")).status_code == 404
