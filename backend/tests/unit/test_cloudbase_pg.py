import asyncio

import httpx
import pytest
from app.providers.cloudbase_pg import (
    CloudBasePgClient,
    CloudBasePgConfigurationError,
    CloudBasePgUnavailable,
)


def test_from_environment_builds_postgrest_base_url(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLOUDBASE_ENV_ID", "travel-split-bill")
    monkeypatch.setenv("CLOUDBASE_API_KEY", "server-key")

    client = CloudBasePgClient.from_environment()

    assert client.base_url == "https://travel-split-bill.api.tcloudbasegateway.com/v1/rdb/rest"


def test_from_environment_rejects_missing_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLOUDBASE_ENV_ID", "travel-split-bill")
    monkeypatch.delenv("CLOUDBASE_API_KEY", raising=False)

    with pytest.raises(CloudBasePgConfigurationError):
        CloudBasePgClient.from_environment()


def test_client_sends_bearer_key_and_maps_503_to_unavailable():
    captured: dict[str, str] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers["authorization"]
        return httpx.Response(503, request=request)

    client = CloudBasePgClient(
        "travel-split-bill",
        "server-key",
        httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )

    with pytest.raises(CloudBasePgUnavailable):
        asyncio.run(client.request("GET", "/trips"))

    assert captured["authorization"] == "Bearer server-key"

def test_from_environment_uses_cloud_hosting_injected_api_key(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("CLOUDBASE_ENV_ID", "travel-split-bill")
    monkeypatch.delenv("CLOUDBASE_API_KEY", raising=False)
    monkeypatch.setenv("CLOUDBASE_APIKEY", "hosting-injected-key")

    client = CloudBasePgClient.from_environment()

    assert client._api_key == "hosting-injected-key"


def test_from_environment_prefers_cloud_hosting_injected_api_key(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("CLOUDBASE_ENV_ID", "travel-split-bill")
    monkeypatch.setenv("CLOUDBASE_API_KEY", "manual-key")
    monkeypatch.setenv("CLOUDBASE_APIKEY", "hosting-injected-key")

    client = CloudBasePgClient.from_environment()

    assert client._api_key == "hosting-injected-key"
