"""Trips created before timezones were stored settle on Beijing time."""

from datetime import UTC, datetime
from types import SimpleNamespace

from app.core.ledger import DEFAULT_TIMEZONE_OFFSET_MINUTES, trip_today
from app.db.models import Trip
from app.db.session import SessionLocal
from test_ledger import auth, expense, pay, preview
from test_ledger import ledger as ledger  # noqa: PLC0414


def forget_timezone(base):
    with SessionLocal() as session:
        session.get(Trip, base.rsplit("/", 1)[1]).timezone_offset_minutes = None
        session.commit()


def test_trip_without_timezone_uses_beijing_days_whatever_the_phone_says():
    from app.core.business_time import client_timezone_offset

    assert DEFAULT_TIMEZONE_OFFSET_MINUTES == -480
    legacy = {"timezone_offset_minutes": None}
    # 16:30 UTC is 00:30 the next day in Beijing.
    late = datetime(2026, 10, 30, 16, 30, tzinfo=UTC)
    assert trip_today(legacy, late) == "2026-10-31"
    assert (
        trip_today(legacy, datetime(2026, 10, 30, 15, 59, tzinfo=UTC)) == "2026-10-30"
    )
    token = client_timezone_offset.set(600)
    try:
        assert trip_today(legacy, late) == "2026-10-31"
    finally:
        client_timezone_offset.reset(token)


def test_trip_without_timezone_keeps_its_amounts_and_can_be_settled(ledger):
    c, base = ledger
    expense(c, base)
    before = c.get(base + "/ledger", headers=auth()).json()["data"]

    forget_timezone(base)

    data = c.get(base + "/ledger", headers=auth()).json()["data"]
    assert data["timezone_offset_minutes"] is None
    assert [(d["status"], d["error"]) for d in data["days"]] == [("pending", "")]
    assert data["summary"] == before["summary"]
    assert "summary_error" not in data
    assert data["summary_incomplete"] is False
    home = c.get("/v1/trips/ledger-summary", headers=auth("b")).json()["data"]
    assert home["groups"] == [
        {"currency": "CNY", "receivable": "0.00", "payable": "200.00"}
    ]
    assert home["trips"][0]["incomplete"] is False
    assert home["trips"][0]["pending"][0]["amount"] == "200.00"
    assert pay(c, base, preview(c, base)).status_code == 201
    day = c.get(base + "/ledger", headers=auth()).json()["data"]["days"][0]
    assert day["status"] == "settled"


def test_today_in_beijing_is_still_recording_for_a_trip_without_timezone(
    ledger, monkeypatch
):
    c, base = ledger
    expense(c, base, day="2026-10-31")
    forget_timezone(base)
    monkeypatch.setattr(
        "app.core.ledger.datetime",
        SimpleNamespace(now=lambda tz: datetime(2026, 10, 30, 16, 30, tzinfo=UTC)),
    )

    data = c.get(base + "/ledger", headers=auth()).json()["data"]

    assert data["business_today"] == "2026-10-31"
    assert data["days"][0]["status"] == "recording"
    blocked = c.post(
        base + "/payments/preview", headers=auth(), json={"dates": ["2026-10-31"]}
    )
    assert blocked.status_code == 422


def test_creator_can_still_set_the_real_timezone_once(ledger):
    c, base = ledger
    expense(c, base)
    forget_timezone(base)
    path = base + "/timezone"

    assert (
        c.patch(
            path, headers=auth("b"), json={"timezone_offset_minutes": -540}
        ).status_code
        == 404
    )
    assert (
        c.patch(
            path, headers=auth(), json={"timezone_offset_minutes": -540}
        ).status_code
        == 200
    )
    assert (
        c.patch(
            path, headers=auth(), json={"timezone_offset_minutes": -480}
        ).status_code
        == 409
    )
    data = c.get(base + "/ledger", headers=auth()).json()["data"]
    assert data["timezone_offset_minutes"] == -540
    assert data["days"][0]["status"] == "pending"
