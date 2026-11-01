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
