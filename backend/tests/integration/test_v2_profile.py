from test_ledger import auth
from test_ledger import ledger as ledger  # noqa: PLC0414


def test_member_nickname_is_user_controlled_and_inviter_can_enter(ledger):
    c, base = ledger
    response = c.patch("/v1/me", headers=auth(), json={"nickname": "小周"})
    assert response.status_code == 200
    members = c.get(base + "/members", headers=auth("b")).json()["data"]
    assert next(m for m in members if m["id"] == "a")["nickname"] == "小周"
    # Use another empty trip so creating an invitation is allowed.
    trip = c.post(
        "/v1/trips", headers=auth(), json={"name": "新旅行", "default_currency": "CNY"}
    ).json()["data"]["id"]
    token = c.post(f"/v1/trips/{trip}/invites", headers=auth()).json()["data"]["token"]
    own = c.get("/v1/trip-invites/" + token, headers=auth()).json()["data"]
    assert own["already_member"] is True and own["inviter_name"] == "小周"
    guest = c.get("/v1/trip-invites/" + token, headers=auth("b")).json()["data"]
    assert guest["already_member"] is False


def test_trip_nickname_is_self_only_and_does_not_change_other_trips(ledger):
    c, base = ledger
    c.patch("/v1/me", headers=auth("b"), json={"nickname": "全局小周"})
    assert (
        c.patch(
            base + "/members/me", headers=auth("b"), json={"nickname": " 东京小周 "}
        ).status_code
        == 200
    )
    assert (
        c.patch(
            base + "/members/me", headers=auth(), json={"nickname": "小林"}
        ).status_code
        == 200
    )
    for user in ["a", "b"]:
        rows = c.get(base + "/members", headers=auth(user)).json()["data"]
        assert {m["id"]: m["nickname"] for m in rows} == {"a": "小林", "b": "东京小周"}
    assert (
        c.patch(
            base + "/members/me", headers=auth("outsider"), json={"nickname": "冒充"}
        ).status_code
        == 404
    )
    assert (
        c.patch(
            base + "/members/me", headers=auth("b"), json={"nickname": "  "}
        ).status_code
        == 422
    )
    other = c.post(
        "/v1/trips",
        headers=auth("b"),
        json={"name": "另一旅行", "default_currency": "CNY"},
    ).json()["data"]["id"]
    assert (
        c.get("/v1/trips/" + other + "/members", headers=auth("b")).json()["data"][0][
            "nickname"
        ]
        == "全局小周"
    )


def test_cloud_trip_nickname_filters_write_by_authenticated_member(monkeypatch):
    from app.main import app
    from fastapi.testclient import TestClient

    class Cloud:
        def __init__(self):
            self.calls = []

        async def request(self, method, path, **kw):
            self.calls.append((method, path, kw))
            return [{"user_id": "b"}] if method == "GET" else None

    cloud = Cloud()
    monkeypatch.setattr(app.state, "cloudbase_pg", cloud, raising=False)
    with TestClient(app) as c:
        r = c.patch(
            "/v1/trips/t/members/me", headers=auth("b"), json={"nickname": "Peer"}
        )
        assert r.status_code == 200
    method, path, kw = cloud.calls[-1]
    assert (method, path) == ("PATCH", "/trip_members")
    assert kw["params"] == {"trip_id": "eq.t", "user_id": "eq.b"}
    assert kw["payload"] == {"nickname": "Peer"}
