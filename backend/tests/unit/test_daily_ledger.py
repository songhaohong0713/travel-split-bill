from copy import deepcopy
from datetime import UTC, datetime

from app.core.ledger import project


def snapshot():
    return {
        "members": ["a", "b"],
        "currency": "CNY",
        "revision": 1,
        "state": {"closed_dates": [], "payments": []},
        "expenses": [record("old", "2020-01-01", "400")],
    }


def record(id, day, value, currency="CNY"):
    return {
        "id": id,
        "revision": 1,
        "occurred_at": day,
        "payload": {
            "expenses": [
                {
                    "expense_id": id,
                    "payer_id": "a",
                    "settlement_currency": currency,
                    "items": [
                        {
                            "item_id": "i",
                            "amount": {"currency": currency, "amount": value},
                            "allocation": {"a": "0.5", "b": "0.5"},
                        }
                    ],
                }
            ]
        },
    }


def paid(s):
    s["state"]["payments"].append(
        {
            "id": "p",
            "currency": "CNY",
            "allocations": [{"date": "2020-01-01", "amount": "200.00"}],
            "basis_expenses": deepcopy(s["expenses"]),
        }
    )


def test_past_days_pending_and_today_recording():
    s = snapshot()
    s["expenses"].append(record("today", datetime.now(UTC).date().isoformat(), "20"))
    days = {d["date"]: d for d in project(s)["days"]}
    assert days["2020-01-01"]["status"] == "pending"
    assert days[datetime.now(UTC).date().isoformat()]["status"] == "recording"


def test_paid_day_frozen_delta_carried_and_undo_restores_original_day():
    s = snapshot()
    paid(s)
    s["expenses"][0] = record("old", "2020-01-01", "460")
    result = project(s)
    days = {d["date"]: d for d in result["days"]}
    assert days["2020-01-01"]["status"] == "settled"
    assert days["2020-01-01"]["pending"] == []
    target = days["2020-01-02"]
    assert target["pending"][0]["amount"] == "30.00"
    assert target["carry_entries"][0]["source_date"] == "2020-01-01"
    s["state"]["payments"][0]["revoked_at"] = "now"
    restored = project(s)["days"]
    assert len(restored) == 1
    assert restored[0]["pending"][0]["amount"] == "230.00"


def test_final_paid_day_creates_next_day_carry_even_after_trip_end(monkeypatch):
    from app.core.ledger import payment_preview

    s = snapshot()
    s["end_date"] = "2020-01-01"
    paid(s)
    s["expenses"][0] = record("old", "2020-01-01", "460")
    monkeypatch.setattr("app.core.ledger.business_today", lambda: "2020-01-02")
    days = {d["date"]: d for d in project(s)["days"]}
    assert days["2020-01-01"]["status"] == "settled"
    assert days["2020-01-02"]["status"] == "recording"
    assert days["2020-01-02"]["carry_entries"][0]["amount"] == "30.00"
    monkeypatch.setattr("app.core.ledger.business_today", lambda: "2020-01-03")
    assert (
        next(d for d in project(s)["days"] if d["date"] == "2020-01-02")["status"]
        == "pending"
    )
    assert payment_preview(s, ["2020-01-02"])["groups"][0]["amount"] == "30.00"


def test_adjustment_can_be_settled_then_edited_again_without_reopening_source():
    from app.core.ledger import payment_preview, settlement_basis

    s = snapshot()
    paid(s)
    s["expenses"][0] = record("old", "2020-01-01", "460")
    s["state"]["closed_dates"].append("2020-01-02")
    p = payment_preview(s)
    basis = settlement_basis(s, p, "CNY")
    s["state"]["payments"].append(
        {
            "id": "p2",
            "currency": "CNY",
            "allocations": p["groups"][0]["allocations"],
            "freeze_basis": basis,
            "depends_on": ["p"],
            "basis_expenses": deepcopy(s["expenses"]),
        }
    )
    assert not payment_preview(s)["groups"]
    s["expenses"][0] = record("old", "2020-01-01", "480")
    s["state"]["closed_dates"].extend(
        d["date"] for d in project(s)["days"] if d["carry_entries"]
    )
    assert payment_preview(s)["groups"][0]["amount"] == "10.00"
    assert (
        next(d for d in project(s)["days"] if d["date"] == "2020-01-01")["status"]
        == "settled"
    )
    s["state"]["payments"][0]["revoked_at"] = "now"
    assert payment_preview(s)["groups"][0]["amount"] == "210.00"


def test_deleted_paid_expense_carries_refund_to_next_pending_day_with_currency_isolation():
    s = snapshot()
    s["expenses"].append(record("yen", "2020-01-01", "1000", "JPY"))
    paid(s)
    s["expenses"] = [
        record("next", "2020-01-02", "100"),
        record("yen", "2020-01-01", "1000", "JPY"),
    ]
    days = {d["date"]: d for d in project(s)["days"]}
    assert days["2020-01-01"]["pending"][0]["currency"] == "JPY"
    assert days["2020-01-02"]["pending"][0]["signed_amount"] == "-150.00"


def test_new_currency_on_fully_settled_day_is_also_carried_forward():
    s = snapshot()
    paid(s)
    s["expenses"].append(record("yen", "2020-01-01", "1000", "JPY"))
    days = {d["date"]: d for d in project(s)["days"]}
    assert days["2020-01-01"]["status"] == "settled"
    assert days["2020-01-02"]["pending"][0]["currency"] == "JPY"


def test_business_day_uses_bounded_client_timezone():
    from app.core.ledger import business_today, client_timezone_offset

    token = client_timezone_offset.set(-540)
    try:
        assert business_today(datetime(2026, 10, 25, 23, tzinfo=UTC)) == "2026-10-26"
    finally:
        client_timezone_offset.reset(token)


def test_future_settled_date_carries_forward_not_back_to_today():
    s = snapshot()
    s["expenses"][0]["occurred_at"] = "2099-01-01"
    paid(s)
    s["state"]["payments"][0]["allocations"][0]["date"] = "2099-01-01"
    s["expenses"][0] = record("old", "2099-01-01", "460")
    days = {d["date"]: d for d in project(s)["days"]}
    assert days["2099-01-01"]["status"] == "recording"
    assert days["2099-01-02"]["carry_entries"][0]["amount"] == "30.00"


def test_fixed_trip_calendar_is_independent_of_request_timezone():
    from app.core.ledger import trip_today

    now = datetime(2026, 10, 30, 16, 30, tzinfo=UTC)
    assert trip_today({"timezone_offset_minutes": -540}, now) == "2026-10-31"
    from app.core.business_time import client_timezone_offset

    token = client_timezone_offset.set(600)
    try:
        assert trip_today({"timezone_offset_minutes": -540}, now) == "2026-10-31"
    finally:
        client_timezone_offset.reset(token)
