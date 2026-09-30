from collections.abc import Generator
from uuid import uuid4

import pytest
from app.api.v1.auth import create_access_token
from app.db.session import configure_database, create_schema, drop_schema
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path) -> Generator[TestClient, None, None]:
    configure_database(f"sqlite+pysqlite:///{tmp_path / 'collaboration.db'}")
    create_schema()
    with TestClient(app) as test_client:
        yield test_client
    drop_schema()


def auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def create_trip(client: TestClient, user_id: str = "owner-a") -> str:
    response = client.post(
        "/v1/trips",
        headers=auth(user_id),
        json={"name": "京都周末", "default_currency": "CNY"},
    )
    assert response.status_code == 201
    return response.json()["data"]["id"]


def expense_payload() -> dict[str, object]:
    return {"occurred_at": "2026-09-19", "payload": {"note": "晚餐"}}


def test_invited_user_can_join_list_and_edit_trip(client: TestClient) -> None:
    trip_id = create_trip(client)
    invite = client.post(f"/v1/trips/{trip_id}/invites", headers=auth("owner-a"))
    assert invite.status_code == 201
    token = invite.json()["data"]["token"]

    accepted = client.post(f"/v1/trip-invites/{token}/accept", headers=auth("peer-b"))
    assert accepted.status_code == 200

    trips = client.get("/v1/trips", headers=auth("peer-b"))
    assert trips.status_code == 200
    assert trips.json()["data"] == [
        {
            "id": trip_id,
            "name": "京都周末",
            "default_currency": "CNY",
            "member_count": 2,
            "is_owner": False,
        }
    ]

    created = client.post(
        f"/v1/trips/{trip_id}/expenses",
        headers={**auth("peer-b"), "Idempotency-Key": str(uuid4())},
        json=expense_payload(),
    )
    assert created.status_code == 201

    listed = client.get(f"/v1/trips/{trip_id}/expenses", headers=auth("owner-a"))
    assert listed.status_code == 200
    assert len(listed.json()["data"]) == 1

    members = client.get(f"/v1/trips/{trip_id}/members", headers=auth("peer-b"))
    assert members.status_code == 200
    assert members.json()["data"] == [
        {"id": "owner-a", "is_owner": True, "is_current": False},
        {"id": "peer-b", "is_owner": False, "is_current": True},
    ]

    preview = client.post(
        f"/v1/trips/{trip_id}/settlements/preview",
        headers=auth("peer-b"),
        json={
            "settlement_currency": "JPY",
            "participants": ["owner-a", "peer-b"],
            "expenses": [{
                "expense_id": "ticket",
                "payer_id": "peer-b",
                "items": [{"item_id": "ticket", "amount": {"currency": "JPY", "amount": "2000"}, "allocation": {"owner-a": "0.5", "peer-b": "0.5"}}],
            }],
        },
    )
    assert preview.status_code == 200
    result = preview.json()["data"]
    assert sum(int(value["amount"]) for value in result["responsibility_by_participant"].values()) == 2000
    assert result["transfers"] == [{"from_participant_id": "owner-a", "to_participant_id": "peer-b", "amount": {"currency": "JPY", "amount": "1000"}}]


def test_trip_invite_can_only_be_accepted_once(client: TestClient) -> None:
    trip_id = create_trip(client)
    token = client.post(
        f"/v1/trips/{trip_id}/invites", headers=auth("owner-a")
    ).json()["data"]["token"]

    assert client.post(f"/v1/trip-invites/{token}/accept", headers=auth("peer-b")).status_code == 200
    repeated = client.post(f"/v1/trip-invites/{token}/accept", headers=auth("third-c"))
    assert repeated.status_code == 410


def test_third_user_cannot_read_or_join_full_trip(client: TestClient) -> None:
    trip_id = create_trip(client)
    token = client.post(
        f"/v1/trips/{trip_id}/invites", headers=auth("owner-a")
    ).json()["data"]["token"]
    client.post(f"/v1/trip-invites/{token}/accept", headers=auth("peer-b"))

    assert client.get(f"/v1/trips/{trip_id}/expenses", headers=auth("third-c")).status_code == 404
    assert client.get(f"/v1/trips/{trip_id}/members", headers=auth("third-c")).status_code == 404


def test_member_can_manage_only_their_own_expense_and_cannot_invite(client: TestClient) -> None:
    trip_id = create_trip(client)
    token = client.post(f"/v1/trips/{trip_id}/invites", headers=auth("owner-a")).json()["data"]["token"]
    assert client.post(f"/v1/trip-invites/{token}/accept", headers=auth("peer-b")).status_code == 200
    own = client.post(f"/v1/trips/{trip_id}/expenses", headers={**auth("peer-b"), "Idempotency-Key": str(uuid4())}, json=expense_payload())
    other = client.post(f"/v1/trips/{trip_id}/expenses", headers={**auth("owner-a"), "Idempotency-Key": str(uuid4())}, json=expense_payload())
    assert own.status_code == other.status_code == 201
    own_id, other_id = own.json()["data"]["id"], other.json()["data"]["id"]
    patch = {"revision": 1, "occurred_at": "2026-09-20", "payload": {"note": "更新"}}
    assert client.patch(f"/v1/trips/{trip_id}/expenses/{own_id}", headers=auth("peer-b"), json=patch).status_code == 200
    assert client.patch(f"/v1/trips/{trip_id}/expenses/{other_id}", headers=auth("peer-b"), json=patch).status_code == 404
    assert client.delete(f"/v1/trips/{trip_id}/expenses/{other_id}", headers=auth("peer-b")).status_code == 404
    assert client.delete(f"/v1/trips/{trip_id}/expenses/{own_id}", headers=auth("peer-b")).status_code == 204
    assert client.post(f"/v1/trips/{trip_id}/invites", headers=auth("peer-b")).status_code == 404
