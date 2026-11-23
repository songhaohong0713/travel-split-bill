"""Placeholder members on the cloud path: what is sent and how answers map."""

from copy import deepcopy

import pytest
from app.api.v1.auth import create_access_token
from app.main import app
from fastapi.testclient import TestClient
from test_ledger_cloudbase import LedgerCloud


class Cloud(LedgerCloud):
    def __init__(self):
        super().__init__()
        self.state["members"] = ["a", "b", "p"]
        self.state["state"] = {"payments": []}
        self.placeholders = [{"user_id": "p", "nickname": "小王"}]
        self.answers = {}
        self.calls = []

    async def request(self, method, path, *, params=None, payload=None):
        self.calls.append((path, params))
        if (
            path == "/trip_members"
            and (params or {}).get("is_placeholder") == "eq.true"
        ):
            return self.placeholders
        if path == "/trip_invites":
            return [{"trip_id": "t", "expires_at": "2099-01-01T00:00:00"}]
        if path == "/trips":
            return [{"id": "t", "name": "京都", "owner_id": "a"}]
        return []

    async def rpc(self, name, payload):
        self.calls.append((name, payload))
        if name in self.answers:
            answer = self.answers[name]
            return answer(payload) if callable(answer) else answer
        return await super().rpc(name, payload)


@pytest.fixture
def cloud(monkeypatch):
    provider = Cloud()
    monkeypatch.setattr(app.state, "cloudbase_pg", provider, raising=False)
    with TestClient(app) as client:
        yield client, provider


def auth(user="a"):
    return {"Authorization": f"Bearer {create_access_token(user)}"}


def sent(provider, name):
    return [payload for called, payload in provider.calls if called == name]


def test_add_sends_a_server_made_id_and_maps_refusals(cloud):
    client, provider = cloud
    provider.answers["tsb_add_placeholder_member"] = {"id": "x", "nickname": "小李"}

    added = client.post(
        "/v1/trips/t/placeholders", headers=auth(), json={"nickname": " 小李 "}
    )

    assert added.status_code == 201
    payload = sent(provider, "tsb_add_placeholder_member")[0]
    assert (payload["p_trip_id"], payload["p_user_id"], payload["p_nickname"]) == (
        "t",
        "a",
        "小李",
    )
    assert added.json()["data"] == {
        "id": payload["p_placeholder_id"],
        "nickname": "小李",
        "is_placeholder": True,
    }
    assert 16 <= len(payload["p_placeholder_id"]) <= 36
    assert payload["p_member_id"] != payload["p_placeholder_id"]
    for answer, status in (({"not_found": True}, 404), ({"full": True}, 409)):
        provider.answers["tsb_add_placeholder_member"] = answer
        refused = client.post(
            "/v1/trips/t/placeholders", headers=auth(), json={"nickname": "小李"}
        )
        assert refused.status_code == status


def test_delete_decides_from_the_snapshot_and_sends_it_back(cloud):
    client, provider = cloud
    provider.state["expenses"][0]["payload"]["participants"] = ["a", "b", "p"]
    provider.answers["tsb_delete_placeholder_member"] = {"ok": True}

    removed = client.delete("/v1/trips/t/placeholders/p", headers=auth())

    assert removed.status_code == 204
    payload = sent(provider, "tsb_delete_placeholder_member")[0]
    assert payload["p_expected"] == provider.state
    assert [e["id"] for e in payload["p_expenses"]] == ["e"]
    assert payload["p_expenses"][0]["payload"]["participants"] == ["a", "b"]
    for answer, status, code in (
        ({"conflict": True}, 409, "TRIP_CHANGED"),
        ({"not_found": True}, 404, "NOT_FOUND"),
    ):
        provider.answers["tsb_delete_placeholder_member"] = answer
        refused = client.delete("/v1/trips/t/placeholders/p", headers=auth())
        assert (refused.status_code, refused.json()["error"]["code"]) == (status, code)
    # In use: refused before anything is sent.
    provider.state["expenses"][0]["payload"]["expenses"][0]["payer_id"] = "p"
    before = len(sent(provider, "tsb_delete_placeholder_member"))
    in_use = client.delete("/v1/trips/t/placeholders/p", headers=auth())
    assert in_use.json()["error"]["code"] == "PLACEHOLDER_IN_USE"
    assert len(sent(provider, "tsb_delete_placeholder_member")) == before


def test_claim_rewrites_the_snapshot_it_was_given_and_maps_every_refusal(cloud):
    client, provider = cloud
    bill = provider.state["expenses"][0]["payload"]["expenses"][0]
    bill["items"][0]["allocation"] = {"a": "0.5", "p": "0.5"}
    old = deepcopy(provider.state)
    provider.answers["tsb_claim_snapshot"] = {"snapshot": old}
    provider.answers["tsb_claim_placeholder_member"] = {
        "id": "t",
        "name": "京都",
        "default_currency": "CNY",
        "member_count": 3,
    }

    claimed = client.post(
        "/v1/trip-invites/token/claim", headers=auth("r"), json={"placeholder_id": "p"}
    )

    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["data"]["member_count"] == 3
    payload = sent(provider, "tsb_claim_placeholder_member")[0]
    assert (payload["p_user_id"], payload["p_placeholder_id"]) == ("r", "p")
    assert payload["p_expected"] == old
    allocation = payload["p_expenses"][0]["payload"]["expenses"][0]["items"][0]
    assert allocation["allocation"] == {"a": "0.5", "r": "0.5"}
    assert payload["p_payments"] == []
    for name in ("tsb_claim_snapshot", "tsb_claim_placeholder_member"):
        for answer, status, code in (
            ({"expired": True}, 410, "INVITE_EXPIRED"),
            ({"already_member": True}, 409, "ALREADY_A_MEMBER"),
            ({"unavailable": True}, 409, "PLACEHOLDER_UNAVAILABLE"),
            ({"conflict": True}, 409, "TRIP_CHANGED"),
        ):
            provider.answers["tsb_claim_snapshot"] = {"snapshot": old}
            provider.answers[name] = answer
            refused = client.post(
                "/v1/trip-invites/token/claim",
                headers=auth("r"),
                json={"placeholder_id": "p"},
            )
            assert (refused.status_code, refused.json()["error"]["code"]) == (
                status,
                code,
            )


def test_invite_preview_lists_placeholders_for_a_newcomer(cloud):
    client, _ = cloud

    preview = client.get("/v1/trip-invites/token", headers=auth("r"))

    assert preview.status_code == 200, preview.text
    assert preview.json()["data"]["placeholders"] == [{"id": "p", "nickname": "小王"}]


def test_payer_marking_payment_to_a_placeholder_commits_a_settlement(cloud):
    client, provider = cloud
    bill = provider.state["expenses"][0]["payload"]["expenses"][0]
    bill["payer_id"] = "p"
    bill["items"][0]["allocation"] = {"p": "0.5", "a": "0.5"}
    preview = client.post(
        "/v1/trips/t/payments/preview", headers=auth(), json={}
    ).json()["data"]

    paid = client.post(
        "/v1/trips/t/payments/mark-paid",
        headers=auth(),
        json={
            "dates": preview["dates"],
            "token": preview["token"],
            "currency": "CNY",
            "from_participant_id": "a",
            "to_participant_id": "p",
        },
    )

    assert paid.status_code == 201, paid.text
    assert paid.json()["data"]["on_behalf"] == {"by": "a", "placeholder": "recipient"}
    committed = sent(provider, "tsb_commit_ledger")[0]["p_state"]
    assert len(committed["payments"]) == 1 and not committed.get("payer_marks")
