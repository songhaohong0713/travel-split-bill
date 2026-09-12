from collections.abc import Generator

import pytest
from app.db.session import configure_database, create_schema, drop_schema
from app.main import app
from fastapi.testclient import TestClient


class FakeWechatAuth:
    async def openid_for_code(self, code: str) -> str:
        assert code == "wechat-login-code"
        return "openid-for-owner"


@pytest.fixture
def client(tmp_path) -> Generator[TestClient, None, None]:
    configure_database(f"sqlite+pysqlite:///{tmp_path / 'auth.db'}")
    create_schema()
    app.state.wechat_auth = FakeWechatAuth()
    with TestClient(app) as test_client:
        yield test_client
    drop_schema()


def test_wechat_login_creates_refreshable_owner_session(client: TestClient) -> None:
    login = client.post("/v1/auth/wechat", json={"code": "wechat-login-code"})

    assert login.status_code == 200
    tokens = login.json()["data"]
    assert set(tokens) == {"access_token", "refresh_token"}
    assert tokens["access_token"] != tokens["refresh_token"]

    refreshed = client.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

    assert refreshed.status_code == 200
    assert refreshed.json()["data"]["refresh_token"] != tokens["refresh_token"]


def test_refresh_token_cannot_be_reused_after_rotation(client: TestClient) -> None:
    tokens = client.post("/v1/auth/wechat", json={"code": "wechat-login-code"}).json()["data"]
    assert client.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 200

    reused = client.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

    assert reused.status_code == 401