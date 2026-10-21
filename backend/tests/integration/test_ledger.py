from uuid import uuid4

import pytest
from app.api.v1.auth import create_access_token
from app.db.session import configure_database, create_schema, drop_schema
from app.main import app
from fastapi.testclient import TestClient


def auth(user="a"):
    return {"Authorization": f"Bearer {create_access_token(user)}"}


@pytest.fixture
def ledger(tmp_path):
    configure_database(f"sqlite+pysqlite:///{tmp_path / 'ledger.db'}")
    create_schema()
    with TestClient(app) as c:
        trip = c.post(
            "/v1/trips",
            headers=auth(),
            json={"name": "东京", "default_currency": "CNY"},
        ).json()["data"]["id"]
        token = c.post(f"/v1/trips/{trip}/invites", headers=auth()).json()["data"][
            "token"
        ]
        c.post(f"/v1/trip-invites/{token}/accept", headers=auth("b"))
        yield c, f"/v1/trips/{trip}"
    drop_schema()


def expense(c, base, amount="400", day="2026-10-07", payer="a", currency="CNY"):
    body = {
        "occurred_at": day,
        "payload": {
            "title": "晚餐",
            "participants": ["a", "b"],
            "expenses": [
                {
                    "expense_id": str(uuid4()),
                    "payer_id": payer,
                    "settlement_currency": currency,
                    "items": [
                        {
                            "item_id": "i",
                            "amount": {"currency": currency, "amount": amount},
                            "allocation": {"a": "0.5", "b": "0.5"},
                        }
                    ],
                }
            ],
        },
    }
    r = c.post(
        base + "/expenses",
        headers={**auth(), "Idempotency-Key": str(uuid4())},
        json=body,
    )
    assert r.status_code == 201
    return r.json()["data"], body


def preview(c, base, dates=None):
    r = c.post(base + "/payments/preview", headers=auth("b"), json={"dates": dates})
    assert r.status_code == 200, r.text
    return r.json()["data"]


def pay(c, base, p):
    return c.post(
        base + "/payments",
        headers=auth("b"),
        json={"dates": p["dates"], "token": p["token"], "currency": "CNY"},
    )


def test_paid_200_then_230_only_30_and_revoke(ledger):
    c, base = ledger
    saved, body = expense(c, base)
    assert (
        c.get(base + "/ledger", headers=auth()).json()["data"]["days"][0]["status"]
        == "recording"
    )
    assert c.post(base + "/days/2026-10-07/close", headers=auth("b")).status_code == 200
    p = preview(c, base)
    assert p["groups"][0]["amount"] == "200.00"
    r = pay(c, base, p)
    assert r.status_code == 201, r.text
    assert r.json()["data"]["from_participant_id"] == "b"
    assert r.json()["data"]["to_participant_id"] == "a"
    assert r.json()["data"]["basis_expenses"][0]["revision"] == 1
    payment_id = r.json()["data"]["id"]
    assert pay(c, base, p).status_code == 409
    body["payload"]["expenses"][0]["items"][0]["amount"]["amount"] = "460"
    assert (
        c.patch(
            base + "/expenses/" + saved["id"],
            headers=auth("b"),
            json={**body, "revision": 1},
        ).status_code
        == 200
    )
    assert preview(c, base)["groups"][0]["amount"] == "30.00"
    assert (
        c.post(base + f"/payments/{payment_id}/revoke", headers=auth()).status_code
        == 200
    )
    assert preview(c, base)["groups"][0]["amount"] == "230.00"


def test_global_offsets_days_and_excludes_recording(ledger):
    c, base = ledger
    expense(c, base, "200", "2026-10-05", "b")
    expense(c, base, "300", "2026-10-06")
    expense(c, base, "1000", "2026-10-07")
    for day in ("2026-10-05", "2026-10-06"):
        c.post(base + f"/days/{day}/close", headers=auth())
    p = preview(c, base)
    assert p["dates"] == ["2026-10-05", "2026-10-06"]
    assert p["groups"][0]["amount"] == "50.00"
    assert pay(c, base, p).status_code == 201
    days = c.get(base + "/ledger", headers=auth()).json()["data"]["days"]
    assert {d["date"]: d["status"] for d in days} == {
        "2026-10-07": "recording",
        "2026-10-06": "settled",
        "2026-10-05": "settled",
    }


def test_stale_preview_and_outsider_rejected(ledger):
    c, base = ledger
    expense(c, base)
    c.post(base + "/days/2026-10-07/close", headers=auth())
    p = preview(c, base)
    expense(c, base, "60")
    assert pay(c, base, p).status_code == 409
    assert c.get(base + "/ledger", headers=auth("outsider")).status_code == 404
    assert (
        c.post(base + "/days/2026-10-07/close", headers=auth("outsider")).status_code
        == 404
    )


def test_overpayment_becomes_reverse_refund_and_currencies_stay_separate(ledger):
    c, base = ledger
    saved, body = expense(c, base)
    c.post(base + "/days/2026-10-07/close", headers=auth())
    assert pay(c, base, preview(c, base)).status_code == 201
    body["payload"]["expenses"][0]["items"][0]["amount"]["amount"] = "360"
    c.patch(
        base + "/expenses/" + saved["id"], headers=auth(), json={**body, "revision": 1}
    )
    group = preview(c, base)["groups"][0]
    assert (
        group["amount"],
        group["from_participant_id"],
        group["to_participant_id"],
    ) == ("20.00", "a", "b")
    expense(c, base, "1000", currency="JPY")
    assert {g["currency"] for g in preview(c, base)["groups"]} == {"JPY", "CNY"}


def test_zero_net_offsets_can_be_settled_and_revoked(ledger):
    c, base = ledger
    for day, payer in [("2026-10-05", "a"), ("2026-10-06", "b")]:
        expense(c, base, "200", day, payer)
        c.post(base + f"/days/{day}/close", headers=auth())
    p = preview(c, base)
    assert p["groups"][0]["offset_only"]
    r = pay(c, base, p)
    assert r.status_code == 201
    assert all(
        d["status"] == "settled"
        for d in c.get(base + "/ledger", headers=auth()).json()["data"]["days"]
    )
    c.post(base + "/payments/" + r.json()["data"]["id"] + "/revoke", headers=auth("b"))
    assert all(
        d["status"] == "pending"
        for d in c.get(base + "/ledger", headers=auth()).json()["data"]["days"]
    )


def test_deleted_paid_expense_leaves_refund_and_trip_can_be_deleted(ledger):
    c, base = ledger
    saved, _ = expense(c, base)
    c.post(base + "/days/2026-10-07/close", headers=auth())
    pay(c, base, preview(c, base))
    assert (
        c.delete(base + "/expenses/" + saved["id"], headers=auth()).status_code == 204
    )
    assert preview(c, base)["groups"][0]["from_participant_id"] == "a"
    assert c.delete(base, headers=auth()).status_code == 204


def test_trip_dates_can_be_saved_and_owner_can_edit(ledger):
    c, base = ledger
    body = {"start_date": "2026-10-05", "end_date": "2026-10-08"}
    assert c.patch(base, headers=auth(), json=body).status_code == 200
    assert (
        c.get("/v1/trips", headers=auth()).json()["data"][0]["start_date"]
        == "2026-10-05"
    )
    assert c.patch(base, headers=auth("b"), json=body).status_code == 404
    assert (
        c.patch(
            base,
            headers=auth(),
            json={"start_date": "2026-10-09", "end_date": "2026-10-08"},
        ).status_code
        == 422
    )


def test_home_summary_never_offsets_separate_trips(ledger):
    c, base = ledger
    expense(c, base, "200")
    c.post(base + "/days/2026-10-07/close", headers=auth())
    trip = c.post(
        "/v1/trips",
        headers=auth(),
        json={"name": "另一段旅行", "default_currency": "CNY"},
    ).json()["data"]["id"]
    other = "/v1/trips/" + trip
    token = c.post(other + "/invites", headers=auth()).json()["data"]["token"]
    c.post("/v1/trip-invites/" + token + "/accept", headers=auth("b"))
    expense(c, other, "300", payer="b")
    c.post(other + "/days/2026-10-07/close", headers=auth())
    result = c.get("/v1/trips/ledger-summary", headers=auth()).json()["data"]
    assert result["groups"] == [
        {"currency": "CNY", "receivable": "100.00", "payable": "150.00"}
    ]
