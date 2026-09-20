from collections.abc import Generator

import pytest
from app.api.v1.auth import create_access_token
from app.main import app
from fastapi.testclient import TestClient


class FakeCloudBasePg:
    def __init__(self) -> None:
        self.rpc_calls: list[tuple[str, dict[str, object]]] = []
        self.request_calls: list[tuple[str, str, dict[str, object] | None]] = []

    async def rpc(self, name: str, payload: dict[str, object]) -> dict[str, object]:
        self.rpc_calls.append((name, payload))
        if name == "tsb_list_member_trips":
            return [{"id": "trip-1", "name": "Tokyo", "default_currency": "JPY", "member_count": 2}]
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

