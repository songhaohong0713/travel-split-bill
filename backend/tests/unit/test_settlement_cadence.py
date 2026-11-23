"""Per-trip settlement cadence: when past days of a trip may be settled."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from app.core.ledger import payment_preview, project
from test_daily_ledger import paid, record, snapshot

FIXTURES = json.loads(
    (
        Path(__file__).parents[1] / "integration/fixtures/phase1_ledger_before.json"
    ).read_text(encoding="utf-8")
)


def trip(end="2020-01-05", today="2020-01-03", monkeypatch=None):
    """Two expenses on past days of a trip settled as a whole."""
    s = snapshot()
    s["expenses"].append(record("second", "2020-01-02", "100"))
    s["settlement_cadence"] = "trip"
    s["settlement_end_date"] = end
    monkeypatch.setattr("app.core.ledger.business_today", lambda: today)
    return s


def statuses(s):
    return {d["date"]: d["status"] for d in project(s)["days"]}


def test_before_the_end_date_every_day_is_recording_and_preview_is_refused(
    monkeypatch,
):
    s = trip(monkeypatch=monkeypatch)

    assert statuses(s) == {"2020-01-01": "recording", "2020-01-02": "recording"}
    with pytest.raises(ValueError, match="整趟结.*2020-01-05"):
        payment_preview(s)
    with pytest.raises(ValueError, match="整趟结.*2020-01-05"):
        payment_preview(s, ["2020-01-01"])


def test_the_end_date_itself_is_still_recording_and_the_next_day_opens(monkeypatch):
    s = trip(end="2020-01-03", monkeypatch=monkeypatch)
    s["expenses"].append(record("last", "2020-01-03", "60"))

    assert set(statuses(s).values()) == {"recording"}

    monkeypatch.setattr("app.core.ledger.business_today", lambda: "2020-01-04")
    assert set(statuses(s).values()) == {"pending"}
    preview = payment_preview(s)
    assert preview["dates"] == ["2020-01-01", "2020-01-02", "2020-01-03"]
    assert preview["groups"][0]["amount"] == "280.00"


def test_after_the_end_date_the_daily_rule_applies_to_later_days(monkeypatch):
    s = trip(end="2020-01-02", today="2020-01-04", monkeypatch=monkeypatch)
    s["expenses"].append(record("late", "2020-01-04", "20"))
    s["expenses"].append(record("later", "2020-01-06", "20"))

    assert statuses(s) == {
        "2020-01-01": "pending",
        "2020-01-02": "pending",
        "2020-01-04": "recording",
        "2020-01-06": "recording",
    }
    with pytest.raises(ValueError, match="今天仍在记录中"):
        payment_preview(s, ["2020-01-04"])


def test_trip_cadence_without_an_end_date_never_opens(monkeypatch):
    s = trip(end=None, today="2099-01-01", monkeypatch=monkeypatch)

    assert set(statuses(s).values()) == {"recording"}
    with pytest.raises(ValueError, match="整趟结.*结束日期"):
        payment_preview(s)


def test_switching_to_daily_opens_past_days_at_once(monkeypatch):
    s = trip(monkeypatch=monkeypatch)
    assert set(statuses(s).values()) == {"recording"}

    s["settlement_cadence"], s["settlement_end_date"] = "daily", None

    assert set(statuses(s).values()) == {"pending"}
    assert payment_preview(s)["groups"][0]["amount"] == "250.00"


def test_days_settled_before_switching_to_trip_stay_settled(monkeypatch):
    s = trip(monkeypatch=monkeypatch)
    s["settlement_cadence"], s["settlement_end_date"] = "daily", None
    paid(s)
    assert statuses(s) == {"2020-01-01": "settled", "2020-01-02": "pending"}

    s["settlement_cadence"], s["settlement_end_date"] = "trip", "2020-01-05"

    days = {d["date"]: d for d in project(s)["days"]}
    assert days["2020-01-01"]["status"] == "settled"
    assert [p["id"] for p in days["2020-01-01"]["settled_transfers"]] == ["p"]
    assert days["2020-01-02"]["status"] == "recording"
    # Nothing new can be settled, and the settled day has nothing left to pay.
    assert payment_preview(s)["groups"] == []
    with pytest.raises(ValueError, match="整趟结"):
        payment_preview(s, ["2020-01-02"])


def test_adjustment_of_a_settled_day_waits_for_the_trip_to_end(monkeypatch):
    s = trip(monkeypatch=monkeypatch)
    s["settlement_cadence"], s["settlement_end_date"] = "daily", None
    paid(s)
    s["settlement_cadence"], s["settlement_end_date"] = "trip", "2020-01-05"

    s["expenses"][0] = record("old", "2020-01-01", "460")

    days = {d["date"]: d for d in project(s)["days"]}
    assert days["2020-01-01"]["status"] == "settled"
    assert days["2020-01-02"]["status"] == "recording"
    assert days["2020-01-02"]["carry_entries"][0]["amount"] == "30.00"
    monkeypatch.setattr("app.core.ledger.business_today", lambda: "2020-01-06")
    assert payment_preview(s)["groups"][0]["amount"] == "80.00"


def test_day_with_broken_data_stays_invalid_under_trip_cadence(monkeypatch):
    s = trip(monkeypatch=monkeypatch)
    s["expenses"][0]["payload"]["expenses"] = []

    assert statuses(s) == {"2020-01-01": "invalid", "2020-01-02": "recording"}


def test_a_day_without_debts_is_recording_until_the_trip_ends(monkeypatch):
    s = trip(monkeypatch=monkeypatch)
    for expense in s["expenses"]:
        expense["payload"]["expenses"][0]["items"][0]["allocation"] = {"a": "1"}

    assert set(statuses(s).values()) == {"recording"}
    monkeypatch.setattr("app.core.ledger.business_today", lambda: "2020-01-06")
    assert set(statuses(s).values()) == {"settled"}


@pytest.mark.parametrize(
    "keys",
    [
        {},
        {"settlement_cadence": "daily"},
        {"settlement_cadence": "daily", "settlement_end_date": None},
        # The end date only matters under the trip cadence.
        {"settlement_cadence": "daily", "settlement_end_date": "2099-12-31"},
        {"settlement_cadence": "unknown", "settlement_end_date": "2099-12-31"},
    ],
)
def test_missing_or_daily_cadence_behaves_exactly_as_before(monkeypatch, keys):
    s = snapshot()
    s["expenses"].append(record("today", "2020-01-03", "20"))
    monkeypatch.setattr("app.core.ledger.business_today", lambda: "2020-01-03")
    expected = project(deepcopy(s))
    expected_preview = payment_preview(deepcopy(s))

    s.update(keys)

    assert project(s) == expected
    preview = payment_preview(s)
    assert {k: v for k, v in preview.items() if k != "token"} == {
        k: v for k, v in expected_preview.items() if k != "token"
    }


@pytest.mark.parametrize("case", FIXTURES, ids=lambda case: case["name"])
def test_existing_trips_keep_their_home_amounts_after_the_migration(monkeypatch, case):
    """Acceptance 3: every stored trip becomes `daily`; nothing it shows moves."""
    from app.api.v1.ledger import trip_summary

    monkeypatch.setattr("app.core.ledger.business_today", lambda: "2026-11-15")
    before = deepcopy(case["snapshot"])
    # Handwritten legacy records lack the timestamp every real payment has.
    for payment in before["state"]["payments"]:
        payment.setdefault("created_at", "2026-11-01T00:00:00+00:00")
    after = {
        **deepcopy(before),
        "settlement_cadence": "daily",
        "settlement_end_date": None,
    }

    assert project(after) == project(before)
    for user in before["members"]:
        assert trip_summary("t", after, user) == trip_summary("t", before, user)
