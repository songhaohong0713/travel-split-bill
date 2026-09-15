import pytest
from app.main import app


@pytest.fixture(autouse=True)
def reset_cloudbase_state():
    if hasattr(app.state, "cloudbase_pg"):
        delattr(app.state, "cloudbase_pg")
    yield
    if hasattr(app.state, "cloudbase_pg"):
        delattr(app.state, "cloudbase_pg")


class HealthyCloudBase:
    async def request(self, method: str, path: str, **kwargs):
        assert method == "GET"
        assert path == "/trips"
        return []


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_readyz_requires_cloudbase_configuration(client):
    app.state.cloudbase_pg = None

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_CONFIGURATION_INVALID"


def test_readyz_verifies_cloudbase_connectivity(client):
    app.state.cloudbase_pg = HealthyCloudBase()

    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}