from copy import deepcopy

from app.api.v1.auth import create_access_token
from app.core.ledger import snapshot_token
from app.main import app
from fastapi.testclient import TestClient


class LedgerCloud:
    def __init__(self):
        self.state = {
            "currency": "CNY",
            "members": ["a", "b"],
            "revision": 1,
            "state": {"closed_dates": ["2026-10-07"], "payments": []},
            "expenses": [
                {
                    "id": "e",
                    "revision": 1,
                    "occurred_at": "2026-10-07",
                    "payload": {
                        "expenses": [
                            {
                                "expense_id": "e",
                                "payer_id": "a",
                                "items": [
                                    {
                                        "item_id": "i",
                                        "amount": {"currency": "CNY", "amount": "400"},
                                        "allocation": {"a": "0.5", "b": "0.5"},
                                    }
                                ],
                            }
                        ]
                    },
                }
            ],
        }
        self.reject_commit = False

    async def rpc(self, name, payload):
        if payload["p_user_id"] not in self.state["members"]:
            return {"not_found": True}
        if name == "tsb_ledger_snapshot":
            return deepcopy(self.state)
        assert name == "tsb_commit_ledger"
        if self.reject_commit or payload["p_expected"] != self.state:
            return {"conflict": True}
        self.state["revision"] += 1
        self.state["state"] = deepcopy(payload["p_state"])
        return {"ok": True}


def test_cloudbase_recomputes_and_commits_expected_snapshot(monkeypatch):
    cloud = LedgerCloud()
    monkeypatch.setattr(app.state, "cloudbase_pg", cloud, raising=False)
    headers = {"Authorization": f"Bearer {create_access_token('a')}"}
    with TestClient(app) as c:
        p = c.post("/v1/trips/t/payments/preview", headers=headers, json={}).json()[
            "data"
        ]
        assert p["token"] == snapshot_token(cloud.state)
        response = c.post(
            "/v1/trips/t/payments",
            headers=headers,
            json={
                "currency": "CNY",
                "token": p["token"],
                "dates": p["dates"],
                "amount": "1",
            },
        )
        assert response.status_code == 201
        assert response.json()["data"]["amount"] == "200.00"
        assert (
            c.get("/v1/trips/t/ledger", headers=headers).json()["data"]["days"][0][
                "status"
            ]
            == "settled"
        )
        assert (
            c.post(
                "/v1/trips/t/payments",
                headers=headers,
                json={"currency": "CNY", "token": p["token"]},
            ).status_code
            == 409
        )


def test_cloudbase_concurrent_change_during_commit_returns_conflict(monkeypatch):
    cloud = LedgerCloud()
    monkeypatch.setattr(app.state, "cloudbase_pg", cloud, raising=False)
    headers = {"Authorization": f"Bearer {create_access_token('a')}"}
    with TestClient(app) as c:
        p = c.post("/v1/trips/t/payments/preview", headers=headers, json={}).json()[
            "data"
        ]
        cloud.reject_commit = True
        assert (
            c.post(
                "/v1/trips/t/payments",
                headers=headers,
                json={"currency": "CNY", "token": p["token"]},
            ).status_code
            == 409
        )
        assert not cloud.state["state"]["payments"]


def test_cloudbase_home_summary_uses_one_batch_rpc(monkeypatch):
    class BatchCloud(LedgerCloud):
        def __init__(self):
            super().__init__()
            self.calls = []

        async def rpc(self, name, payload):
            self.calls.append(name)
            assert name == "tsb_member_ledger_snapshots"
            assert payload == {"p_user_id": "a"}
            return [{"trip_id": "t", **deepcopy(self.state)}]

    cloud = BatchCloud()
    monkeypatch.setattr(app.state, "cloudbase_pg", cloud, raising=False)
    headers = {"Authorization": f"Bearer {create_access_token('a')}"}
    with TestClient(app) as client:
        response = client.get("/v1/trips/ledger-summary", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["groups"] == [
        {"currency": "CNY", "receivable": "200.00", "payable": "0.00"}
    ]
    assert cloud.calls == ["tsb_member_ledger_snapshots"]
