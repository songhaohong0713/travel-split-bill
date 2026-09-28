from collections.abc import Generator

import pytest
from app.api.v1.auth import create_access_token
from app.main import app
from app.providers.cloudbase_pg import CloudBasePgRequestError
from fastapi.testclient import TestClient


class FakeCloudBasePg:
    def __init__(self) -> None:
        self.rpc_calls: list[tuple[str, dict[str, object]]] = []
        self.request_calls: list[tuple[str, str, dict[str, object] | None]] = []

    async def rpc(self, name: str, payload: dict[str, object]) -> dict[str, object]:
        self.rpc_calls.append((name, payload))
        if name == "tsb_list_member_trips":
            return [{"id": "trip-1", "name": "Tokyo", "default_currency": "JPY", "member_count": 2}]
        if name.startswith("tsb_delete_owner_"):
            return {"deleted": True}
        return {"id": payload["p_trip_id"], "name": payload["p_name"], "default_currency": payload["p_default_currency"]}

    async def request(
        self, method: str, path: str, *, params: dict[str, object] | None = None, payload: object = None
    ) -> list[dict[str, str]]:
        self.request_calls.append((method, path, params))
        return [{"id": "trip-1", "name": "Tokyo", "default_currency": "JPY"}]


@pytest.fixture
def client() -> Generator[tuple[TestClient, FakeCloudBasePg], None, None]:
    provider = FakeCloudBasePg()
    app.state.cloudbase_pg = provider
    with TestClient(app) as test_client:
        yield test_client, provider
    delattr(app.state, "cloudbase_pg")


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token('owner-1')}"}


def test_trips_use_cloudbase_rpc_for_mutation_and_table_api_for_reading(
    client: tuple[TestClient, FakeCloudBasePg],
) -> None:
    test_client, provider = client

    created = test_client.post(
        "/v1/trips", json={"name": "Tokyo", "default_currency": "JPY"}, headers=_headers()
    )
    listed = test_client.get("/v1/trips", headers=_headers())

    assert created.status_code == 201
    assert provider.rpc_calls[0][0] == "tsb_create_trip"
    assert listed.status_code == 200
    assert provider.rpc_calls[1] == ("tsb_list_member_trips", {"p_user_id": "owner-1"})


def test_cloudbase_expense_revision_conflict_returns_409(
    client: tuple[TestClient, FakeCloudBasePg],
) -> None:
    test_client, provider = client

    async def conflicting_rpc(name: str, payload: dict[str, object]) -> dict[str, object]:
        provider.rpc_calls.append((name, payload))
        raise CloudBasePgRequestError(400, "TSB_REVISION_CONFLICT")

    provider.rpc = conflicting_rpc  # type: ignore[method-assign]
    response = test_client.patch(
        "/v1/trips/trip-1/expenses/expense-1",
        headers=_headers(),
        json={"revision": 1, "occurred_at": "2026-09-28", "payload": {}},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REVISION_CONFLICT"


def test_cloudbase_owner_deletion_uses_delete_rpcs(
    client: tuple[TestClient, FakeCloudBasePg],
) -> None:
    test_client, provider = client

    expense = test_client.delete("/v1/trips/trip-1/expenses/expense-1", headers=_headers())
    trip = test_client.delete("/v1/trips/trip-1", headers=_headers())

    assert expense.status_code == trip.status_code == 204
    assert provider.rpc_calls == [
        ("tsb_delete_owner_expense", {"p_trip_id": "trip-1", "p_expense_id": "expense-1", "p_owner_id": "owner-1"}),
        ("tsb_delete_owner_trip", {"p_trip_id": "trip-1", "p_owner_id": "owner-1"}),
    ]

