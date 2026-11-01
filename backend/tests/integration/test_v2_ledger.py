from datetime import UTC, datetime

from test_ledger import (
    auth,
    expense,
    pay,
    preview,
)
from test_ledger import ledger as ledger  # noqa: PLC0414


def test_jpy_trip_keeps_jpy_after_create_save_read_and_settlement_preview(ledger):
    c, _ = ledger
    trip = c.post(
        "/v1/trips",
        headers=auth(),
        json={"name": "日元结算回归", "default_currency": "JPY"},
    ).json()["data"]
    base = "/v1/trips/" + trip["id"]
    token = c.post(base + "/invites", headers=auth()).json()["data"]["token"]
    assert (
        c.post("/v1/trip-invites/" + token + "/accept", headers=auth("b")).status_code
        == 200
    )
    saved, _ = expense(c, base, amount="1234", currency="JPY")
    records = c.get(base + "/expenses", headers=auth()).json()["data"]
    assert (
        next(r for r in records if r["id"] == saved["id"])["payload"]["expenses"][0][
            "settlement_currency"
        ]
        == "JPY"
    )
    view = c.get(base + "/ledger", headers=auth()).json()["data"]
    assert [g["currency"] for g in view["days"][0]["groups"]] == ["JPY"]
    p = preview(c, base)
    assert [(g["currency"], g["amount"]) for g in p["groups"]] == [("JPY", "617")]


def test_renaming_a_settled_legacy_item_does_not_change_money_or_generate_carry(ledger):
    c, base = ledger
    saved, body = expense(c, base, amount="3.00", currency="JPY")
    bill = body["payload"]["expenses"][0]
    bill.update(settlement_currency="CNY", reference_rate="1")
    assert (
        c.patch(
            base + "/expenses/" + saved["id"],
            headers=auth(),
            json={**body, "revision": 1},
        ).status_code
        == 200
    )
    p = preview(c, base)
    assert p["groups"][0]["amount"] == "1.50"
    assert pay(c, base, p).status_code == 201
    before = c.get(base + "/ledger", headers=auth()).json()["data"]
    bill["items"][0]["name"] = "只改商品名称"
    body["payload"]["title"] = "只改商户标题"
    response = c.patch(
        base + "/expenses/" + saved["id"], headers=auth(), json={**body, "revision": 2}
    )
    assert response.status_code == 200, response.text
    after = c.get(base + "/ledger", headers=auth()).json()["data"]
    assert after["days"][0]["groups"] == before["days"][0]["groups"]
    assert after["summary"] == []
    assert len(after["days"]) == 1
    assert after["days"][0]["status"] == "settled"
    assert not after["days"][0]["carry_entries"]


def test_combined_mark_cannot_be_used_for_a_different_subset(ledger):
    c, base = ledger
    expense(c, base, amount="20", day="2026-10-07")
    expense(c, base, amount="20", day="2026-10-08")
    combined = preview(c, base)
    marked = c.post(
        base + "/payments/mark-paid",
        headers=auth("b"),
        json={
            "dates": combined["dates"],
            "token": combined["token"],
            "currency": "CNY",
        },
    )
    assert marked.status_code == 201
    single = preview(c, base, ["2026-10-07"])
    body = {"dates": single["dates"], "token": single["token"], "currency": "CNY"}
    response = c.post(
        base + "/payments",
        headers=auth(),
        json={**body, "mark_id": marked.json()["data"]["id"]},
    )
    assert response.status_code == 409
    assert c.get(base + "/ledger", headers=auth()).json()["data"]["payments"] == []
    assert c.post(base + "/payments", headers=auth(), json=body).status_code == 201
    after = c.get(base + "/ledger", headers=auth()).json()["data"]
    assert {d["date"]: d["status"] for d in after["days"]} == {
        "2026-10-07": "settled",
        "2026-10-08": "pending",
    }
    assert [a["date"] for a in after["payments"][0]["allocations"]] == ["2026-10-07"]
    assert after["summary"][0]["amount"] == "10.00"


def test_editing_the_final_settled_trip_day_creates_payable_carry_after_trip_end(
    ledger,
):
    c, base = ledger
    assert (
        c.patch(
            base,
            headers=auth(),
            json={"start_date": "2026-10-07", "end_date": "2026-10-07"},
        ).status_code
        == 200
    )
    saved, body = expense(c, base, amount="400", day="2026-10-07")
    assert pay(c, base, preview(c, base)).status_code == 201
    body["payload"]["expenses"][0]["items"][0]["amount"]["amount"] = "460"
    response = c.patch(
        base + "/expenses/" + saved["id"], headers=auth(), json={**body, "revision": 1}
    )
    assert response.status_code == 200, response.text
    after = c.get(base + "/ledger", headers=auth()).json()["data"]
    days = {d["date"]: d for d in after["days"]}
    assert days["2026-10-07"]["status"] == "settled"
    assert days["2026-10-08"]["status"] == "pending"
    assert days["2026-10-08"]["carry_entries"][0]["source_date"] == "2026-10-07"
    p = preview(c, base, ["2026-10-08"])
    assert p["groups"][0]["amount"] == "30.00"


def test_payer_mark_waits_for_recipient_and_rejects_stale_mark(ledger):
    c, base = ledger
    expense(c, base)
    p = preview(c, base)
    body = {"dates": p["dates"], "token": p["token"], "currency": "CNY"}
    assert c.post(base + "/payments", headers=auth("b"), json=body).status_code == 403
    assert (
        c.post(base + "/payments/mark-paid", headers=auth("a"), json=body).status_code
        == 403
    )
    marked = c.post(base + "/payments/mark-paid", headers=auth("b"), json=body)
    assert marked.status_code == 201, marked.text
    mark = marked.json()["data"]
    result = c.get(base + "/ledger", headers=auth()).json()["data"]
    assert result["days"][0]["status"] == "pending"
    assert result["payer_marks"][0]["id"] == mark["id"]
    assert result["payments"] == []
    assert (
        c.post(
            base + "/payments", headers=auth(), json={**body, "mark_id": mark["id"]}
        ).status_code
        == 409
    )
    p = preview(c, base)
    confirmed = c.post(
        base + "/payments",
        headers=auth(),
        json={**body, "token": p["token"], "mark_id": mark["id"]},
    )
    assert confirmed.status_code == 201, confirmed.text
    result = c.get(base + "/ledger", headers=auth()).json()["data"]
    assert result["days"][0]["status"] == "settled"
    assert (
        result["payer_marks"][0]["confirmed_payment_id"]
        == confirmed.json()["data"]["id"]
    )


def test_today_cannot_close_or_settle(ledger):
    c, base = ledger
    today = datetime.now(UTC).date().isoformat()
    expense(c, base, day=today)
    assert c.post(base + "/days/" + today + "/close", headers=auth()).status_code == 422
    assert (
        c.post(
            base + "/payments/preview", headers=auth(), json={"dates": [today]}
        ).status_code
        == 422
    )


def test_offline_trip_uuid_replay_is_owner_and_payload_scoped(ledger):
    from uuid import uuid4

    c, _ = ledger
    body = {"name": "离线旅行", "default_currency": "CNY", "client_id": str(uuid4())}
    first = c.post("/v1/trips", headers=auth(), json=body)
    assert first.status_code == 201
    assert first.json()["data"]["id"] == body["client_id"]
    assert c.post("/v1/trips", headers=auth(), json=body).json() == first.json()
    assert (
        c.post("/v1/trips", headers=auth(), json={**body, "name": "不同"}).status_code
        == 409
    )
    assert c.post("/v1/trips", headers=auth("b"), json=body).status_code == 409


def test_server_expense_summary_uses_saved_algorithm(ledger):
    c, base = ledger
    saved, _ = expense(c, base)
    data = c.get(base + "/ledger", headers=auth()).json()["data"]
    summary = data["expense_summaries"][0]
    assert summary["id"] == saved["id"]
    assert summary["groups"][0]["paid_by_participant"]["a"]["amount"] == "400.00"
    assert data["totals"][0] == {"currency": "CNY", "amount": "400.00"}


def test_mark_is_revocable_and_cannot_confirm_after_expense_change(ledger):
    c, base = ledger
    saved, body = expense(c, base)
    p = preview(c, base)
    mark = c.post(
        base + "/payments/mark-paid",
        headers=auth("b"),
        json={"dates": p["dates"], "token": p["token"], "currency": "CNY"},
    ).json()["data"]
    assert (
        c.post(
            base + "/payer-marks/" + mark["id"] + "/revoke", headers=auth()
        ).status_code
        == 403
    )
    body["payload"]["expenses"][0]["items"][0]["amount"]["amount"] = "420"
    assert (
        c.patch(
            base + "/expenses/" + saved["id"],
            headers=auth(),
            json={**body, "revision": 1},
        ).status_code
        == 200
    )
    p = preview(c, base)
    assert (
        c.post(
            base + "/payments",
            headers=auth(),
            json={
                "dates": p["dates"],
                "token": p["token"],
                "currency": "CNY",
                "mark_id": mark["id"],
            },
        ).status_code
        == 409
    )
    result = c.get(base + "/ledger", headers=auth()).json()["data"]
    assert result["payer_marks"][0]["stale"] is True
    assert (
        c.post(
            base + "/payer-marks/" + mark["id"] + "/revoke", headers=auth("b")
        ).status_code
        == 200
    )
    assert not c.get(base + "/ledger", headers=auth()).json()["data"]["payments"]


def test_v2_summary_matches_past_days_and_excludes_today(ledger):
    c, base = ledger
    expense(c, base, day="2026-09-29")
    expense(c, base, day="2026-09-30")
    today = datetime.now(UTC).date().isoformat()
    expense(c, base, day=today)
    detail = c.get(base + "/ledger", headers=auth("b")).json()["data"]
    summary = c.get("/v1/trips/ledger-summary", headers=auth("b")).json()["data"]
    assert detail["contract_version"] == summary["contract_version"] == 2
    assert [d["status"] for d in detail["days"] if d["date"] < today] == [
        "pending",
        "pending",
    ]
    assert (
        next(d for d in detail["days"] if d["date"] == today)["status"] == "recording"
    )
    trip = next(t for t in summary["trips"] if t["id"] == base.split("/")[-1])
    assert trip["expense_count"] == 3
    assert trip["day_count"] == 3
    assert trip["has_recording"]
    assert trip["pending"][0]["amount"] == detail["summary"][0]["amount"]
    assert trip["pending"][0]["direction"] == "payable"


def test_dependent_payment_must_be_revoked_first(ledger):
    c, base = ledger
    first, body = expense(c, base, amount="200", day="2026-10-07")
    expense(c, base, amount="40", day="2026-10-08")
    p1 = pay(c, base, preview(c, base, ["2026-10-07"])).json()["data"]
    body["payload"]["expenses"][0]["items"][0]["amount"]["amount"] = "220"
    assert (
        c.patch(
            base + "/expenses/" + first["id"],
            headers=auth(),
            json={**body, "revision": 1},
        ).status_code
        == 200
    )
    p2 = pay(c, base, preview(c, base, ["2026-10-08"])).json()["data"]
    assert p1["id"] in p2["depends_on"]
    before = c.get(base + "/ledger", headers=auth()).json()["data"]
    blocked = c.post(base + "/payments/" + p1["id"] + "/revoke", headers=auth())
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "PAYMENT_DEPENDENCY"
    assert c.get(base + "/ledger", headers=auth()).json()["data"] == before
    assert (
        c.post(base + "/payments/" + p2["id"] + "/revoke", headers=auth()).status_code
        == 200
    )
    assert (
        c.post(base + "/payments/" + p1["id"] + "/revoke", headers=auth()).status_code
        == 200
    )
    assert preview(c, base)["groups"][0]["amount"] == "130.00"


def test_new_payment_uses_compact_basis_and_skips_settled_zero_dates(ledger):
    c, base = ledger
    expense(c, base, amount="200", day="2026-10-07")
    expense(c, base, amount="40", day="2026-10-08")
    p1 = pay(c, base, preview(c, base, ["2026-10-07"])).json()["data"]
    assert "basis_expenses" not in p1
    assert p1["basis_currencies"] == {"2026-10-07": ["CNY"]}
    p = preview(c, base, ["2026-10-07", "2026-10-08"])
    assert p["dates"] == ["2026-10-08"]
    assert p["groups"][0]["allocations"] == [{"date": "2026-10-08", "amount": "20.00"}]
    assert pay(c, base, p).status_code == 201
    assert preview(c, base)["groups"] == []


def test_historical_confirmation_cannot_be_claimed_by_the_payer(ledger):
    c, base = ledger
    expense(c, base, amount="3", payer="b")
    assert c.post(base + "/history/prepare", headers=auth()).status_code == 200
    assert (
        c.post(
            base + "/history/preview", headers=auth(), json={"mode": "settled"}
        ).status_code
        == 403
    )
    result = c.get(base + "/ledger", headers=auth()).json()["data"]
    assert result["payments"] == []
    assert preview(c, base)["groups"][0]["amount"] == "1.50"


import pytest


@pytest.mark.parametrize(
    "kind,expected,debtor",
    [
        ("amount", "1.00", "a"),
        ("payer", "3.00", "b"),
        ("allocation", "0.75", "a"),
        ("same_net", None, None),
        ("title", None, None),
        ("actual", "1.00", "a"),
    ],
)
def test_confirmed_payment_survives_edits_and_only_net_difference_is_carried(
    ledger, kind, expected, debtor
):
    c, base = ledger
    saved, body = expense(c, base, amount="3", payer="b")
    bill = body["payload"]["expenses"][0]
    revision = 1
    if kind == "actual":
        bill["items"][0]["amount"] = {"currency": "JPY", "amount": "300"}
        bill["actual_payment"] = {"currency": "USD", "amount": "0.50"}
        bill["payment_to_settlement_rate"] = "6"
        assert (
            c.patch(
                base + "/expenses/" + saved["id"],
                headers=auth(),
                json={**body, "revision": revision},
            ).status_code
            == 200
        )
        revision += 1
    payment = pay(c, base, preview(c, base)).json()["data"]
    assert payment["amount"] == "1.50"
    if kind == "amount":
        bill["items"][0]["amount"]["amount"] = "5"
    elif kind == "payer":
        bill["payer_id"] = "a"
    elif kind == "allocation":
        bill["items"][0]["allocation"] = {"a": "0.75", "b": "0.25"}
    elif kind == "same_net":
        bill["items"][0]["amount"]["amount"] = "4"
        bill["items"][0]["allocation"] = {"a": "0.375", "b": "0.625"}
    elif kind == "title":
        body["payload"]["title"] = "renamed"
        body["payload"]["note"] = "metadata only"
    elif kind == "actual":
        bill["payment_to_settlement_rate"] = "10"
    path = base + "/expenses/" + saved["id"]
    response = c.patch(path, headers=auth("b"), json={**body, "revision": revision})
    assert response.status_code == 200, response.text
    assert (
        c.patch(path, headers=auth(), json={**body, "revision": revision}).status_code
        == 409
    )
    assert (
        c.patch(
            path, headers=auth(), json={**body, "revision": revision + 1}
        ).status_code
        == 200
    )
    data = c.get(base + "/ledger", headers=auth()).json()["data"]
    assert data["payments"] == [payment]
    original = next(d for d in data["days"] if d["date"] == "2026-10-07")
    assert original["status"] == "settled"
    carries = [d for d in data["days"] if d["carry_entries"]]
    if expected:
        assert len(carries) == 1
        pending = carries[0]["pending"][0]
        assert (pending["amount"], pending["from_participant_id"]) == (expected, debtor)
        assert preview(c, base)["groups"][0]["amount"] == expected
        summary = c.get("/v1/trips/ledger-summary", headers=auth()).json()["data"]
        assert summary["trips"][0]["pending"][0]["amount"] == expected
    else:
        assert carries == []
        assert data["summary"] == []


@pytest.mark.parametrize("failure_path", ["/trip_members", "/trips", "/users"])
def test_invite_auxiliary_database_failure_is_service_unavailable(
    monkeypatch, failure_path
):
    from app.main import app
    from app.providers.cloudbase_pg import CloudBasePgUnavailable
    from fastapi.testclient import TestClient

    class Cloud:
        async def request(self, method, path, **kwargs):
            if path == failure_path:
                raise CloudBasePgUnavailable("synthetic unavailable")
            if path == "/trip_invites":
                return [{"trip_id": "t", "expires_at": "2099-01-01T00:00:00+00:00"}]
            if path == "/trip_members":
                return []
            if path == "/trips":
                return [{"id": "t", "name": "Trip", "owner_id": "a"}]
            return []

    monkeypatch.setattr(app.state, "cloudbase_pg", Cloud(), raising=False)
    with TestClient(app) as c:
        r = c.get("/v1/trip-invites/synthetic-token", headers=auth("b"))
        assert r.status_code == 503, r.text


def test_partial_historical_payments_that_finish_a_day_preserve_dependencies(ledger):
    c, base = ledger
    expense(c, base, amount="200")
    c.post(base + "/history/prepare", headers=auth())
    selection = {
        "mode": "partial",
        "payments": [
            {
                "date": "2026-10-07",
                "currency": "CNY",
                "amount": n,
                "from_participant_id": "b",
                "to_participant_id": "a",
            }
            for n in ["70", "30"]
        ],
    }
    proposal = c.post(base + "/history/preview", headers=auth(), json=selection).json()[
        "data"
    ]
    r = c.post(
        base + "/history/confirm",
        headers=auth(),
        json={**selection, "token": proposal["token"]},
    )
    assert r.status_code == 200, r.text
    payments = c.get(base + "/ledger", headers=auth()).json()["data"]["payments"]
    assert (
        c.post(
            base + "/payments/" + payments[0]["id"] + "/revoke", headers=auth()
        ).status_code
        == 409
    )
    assert (
        c.post(
            base + "/payments/" + payments[1]["id"] + "/revoke", headers=auth()
        ).status_code
        == 200
    )
    assert preview(c, base)["groups"][0]["amount"] == "30.00"


def test_non_owner_recipient_can_confirm_historical_receipt(ledger):
    c, base = ledger
    expense(c, base, amount="3", payer="b")
    c.post(base + "/history/prepare", headers=auth())
    p = c.post(base + "/history/preview", headers=auth("b"), json={"mode": "settled"})
    assert p.status_code == 200, p.text
    r = c.post(
        base + "/history/confirm",
        headers=auth("b"),
        json={"mode": "settled", "token": p.json()["data"]["token"]},
    )
    assert r.status_code == 200, r.text
    assert preview(c, base)["groups"] == []


def test_trip_timezone_is_frozen_at_creation_and_legacy_can_be_set_only_by_owner(
    ledger,
):
    from app.db.models import Trip
    from app.db.session import SessionLocal

    c, base = ledger
    c.post(
        "/v1/trips",
        headers={**auth(), "X-Client-Timezone-Offset": "-540"},
        json={"name": "Tokyo TZ", "default_currency": "JPY"},
    ).json()["data"]
    trip = next(
        t
        for t in c.get("/v1/trips", headers=auth()).json()["data"]
        if t["name"] == "Tokyo TZ"
    )
    assert trip["timezone_offset_minutes"] == -540
    path = base + "/timezone"
    assert (
        c.patch(
            path, headers=auth("b"), json={"timezone_offset_minutes": -480}
        ).status_code
        == 404
    )
    with SessionLocal() as s:
        row = s.get(Trip, base.rsplit("/", 1)[-1])
        row.timezone_offset_minutes = None
        s.commit()
    assert (
        c.post(base + "/payments/preview", headers=auth(), json={}).status_code == 422
    )
    assert (
        c.patch(
            path, headers=auth(), json={"timezone_offset_minutes": -480}
        ).status_code
        == 200
    )
    assert (
        c.patch(path, headers=auth(), json={"timezone_offset_minutes": 0}).status_code
        == 409
    )


def test_manipulating_client_timezone_cannot_end_a_fixed_trip_day(ledger, monkeypatch):
    from types import SimpleNamespace

    c, base = ledger
    expense(c, base, amount="3", day="2026-10-30")
    monkeypatch.setattr(
        "app.core.ledger.datetime",
        SimpleNamespace(now=lambda tz: datetime(2026, 10, 30, 23, 30, tzinfo=UTC)),
    )
    for offset in [-840, 720]:
        headers = {**auth(), "X-Client-Timezone-Offset": str(offset)}
        r = c.post(
            base + "/payments/preview", headers=headers, json={"dates": ["2026-10-30"]}
        )
        assert r.status_code == 422
        day = c.get(base + "/ledger", headers=headers).json()["data"]["days"][0]
        assert day["status"] == "recording"
