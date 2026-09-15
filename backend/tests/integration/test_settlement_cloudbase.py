from collections.abc import Generator

import pytest
from app.api.v1.auth import create_access_token
from app.main import app
from fastapi.testclient import TestClient


class FakeCloudBasePg:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def rpc(self, name: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((name, payload))
        return {"id": payload["p_version_id"], "trip_id": payload["p_trip_id"], "result": payload["p_result"]}


@pytest.fixture
def client() -> Generator[tuple[TestClient, FakeCloudBasePg], None, None]:
    provider = FakeCloudBasePg()
    app.state.cloudbase_pg = provider
    with TestClient(app) as test_client:
        yield test_client, provider
    delattr(app.state, "cloudbase_pg")


def test_publish_settlement_uses_cloudbase_rpc(client: tuple[TestClient, FakeCloudBasePg]) -> None:
    test_client, provider = client
    response = test_client.post(
        "/v1/trips/trip-1/settlements/publish",
        headers={"Authorization": f"Bearer {create_access_token('owner-1')}"},
        json={"participants": ["a", "b"], "settlement_currency": "CNY", "expenses": []},
    )

    assert response.status_code == 201
    assert provider.calls[0][0] == "tsb_publish_settlement_version"
