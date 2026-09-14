from collections.abc import Generator

import pytest
from app.main import app
from fastapi.testclient import TestClient


class FakeWechatAuth:
    async def openid_for_code(self, code: str) -> str:
        assert code == "wechat-login-code"
        return "openid-for-owner"


class FakeCloudBasePg:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def rpc(self, name: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((name, payload))
        return {"user_id": payload["p_user_id"]}


@pytest.fixture
def client() -> Generator[tuple[TestClient, FakeCloudBasePg], None, None]:
    provider = FakeCloudBasePg()
    app.state.wechat_auth = FakeWechatAuth()
    app.state.cloudbase_pg = provider
    with TestClient(app) as test_client:
        yield test_client, provider
    delattr(app.state, "cloudbase_pg")


def test_wechat_login_persists_with_cloudbase_rpc(
    client: tuple[TestClient, FakeCloudBasePg],
) -> None:
    test_client, provider = client

    response = test_client.post("/v1/auth/wechat", json={"code": "wechat-login-code"})

    assert response.status_code == 200
    assert provider.calls[0][0] == "tsb_upsert_wechat_user_and_issue_refresh_token"
    assert provider.calls[0][1]["p_openid_hash"]
    assert provider.calls[0][1]["p_token_hash"]
