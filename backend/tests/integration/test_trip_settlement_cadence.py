"""Phase 4: a trip is settled every day or once, after its end date."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from test_ledger import auth, expense, pay, preview
from test_ledger import ledger as ledger  # noqa: PLC0414

TODAY = datetime.now(UTC).date()
YESTERDAY = (TODAY - timedelta(days=1)).isoformat()
TOMORROW = (TODAY + timedelta(days=1)).isoformat()
LAST_WEEK = (TODAY - timedelta(days=7)).isoformat()


def set_dates(c, base, end, start=None, user="a"):
    return c.patch(
        base, headers=auth(user), json={"start_date": start, "end_date": end}
    )


def set_cadence(c, base, cadence, user="a"):
    return c.patch(
        base + "/settlement-cadence",
        headers=auth(user),
        json={"settlement_cadence": cadence},
    )


def trip_row(c, base, user="a"):
    rows = c.get("/v1/trips", headers=auth(user)).json()["data"]
    return next(r for r in rows if r["id"] == base.rsplit("/", 1)[1])


def day(c, base, date=YESTERDAY):
    days = c.get(base + "/ledger", headers=auth()).json()["data"]["days"]
    return next(d for d in days if d["date"] == date)


def test_trip_ending_tomorrow_keeps_yesterday_recording_until_switched_to_daily(
    ledger,
):
    c, base = ledger
    expense(c, base, day=YESTERDAY)
    assert day(c, base)["status"] == "pending"
    assert set_dates(c, base, TOMORROW).status_code == 200

    switched = set_cadence(c, base, "trip")

    assert switched.status_code == 200, switched.text
    assert switched.json()["data"] == {"settlement_cadence": "trip"}
    assert trip_row(c, base)["settlement_cadence"] == "trip"
    assert trip_row(c, base, "b")["settlement_cadence"] == "trip"
    assert day(c, base)["status"] == "recording"
    for body in ({}, {"dates": [YESTERDAY]}):
        refused = c.post(base + "/payments/preview", headers=auth("b"), json=body)
        assert refused.status_code == 422
        assert refused.json()["error"]["code"] == "INVALID_SETTLEMENT_SCOPE"
        assert "整趟结" in refused.json()["error"]["message"]
        assert TOMORROW in refused.json()["error"]["message"]
    home = c.get("/v1/trips/ledger-summary", headers=auth("b")).json()["data"]
    assert home["groups"] == []
    assert home["trips"][0]["pending"] == []
    assert home["trips"][0]["pending_days"] == 0
    assert home["trips"][0]["has_recording"] is True
    assert home["trips"][0]["incomplete"] is False

    assert set_cadence(c, base, "daily").status_code == 200

    assert day(c, base)["status"] == "pending"
    assert preview(c, base)["groups"][0]["amount"] == "200.00"
    home = c.get("/v1/trips/ledger-summary", headers=auth("b")).json()["data"]
    assert home["groups"] == [
        {"currency": "CNY", "receivable": "0.00", "payable": "200.00"}
    ]


def test_trip_cadence_opens_the_day_after_the_end_date(ledger):
    c, base = ledger
    expense(c, base, day=LAST_WEEK)
    expense(c, base, day=YESTERDAY)
    assert set_dates(c, base, YESTERDAY).status_code == 200

    assert set_cadence(c, base, "trip").status_code == 200

    assert day(c, base, LAST_WEEK)["status"] == "pending"
    assert day(c, base)["status"] == "pending"
    assert preview(c, base)["groups"][0]["amount"] == "400.00"
    # Extending the trip closes the window again, at once.
    assert set_dates(c, base, TODAY.isoformat()).status_code == 200
    assert day(c, base)["status"] == "recording"
    assert (
        c.post(base + "/payments/preview", headers=auth(), json={}).status_code == 422
    )


def test_only_the_creator_changes_the_cadence_and_trip_needs_an_end_date(ledger):
    c, base = ledger
    assert trip_row(c, base)["settlement_cadence"] == "daily"

    assert set_cadence(c, base, "trip", user="b").status_code == 404
    assert set_cadence(c, base, "trip", user="outsider").status_code == 404
    assert set_cadence(c, base, "weekly").status_code == 422
    missing = set_cadence(c, base, "trip")
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "END_DATE_REQUIRED"
    assert "结束日期" in missing.json()["error"]["message"]
    assert trip_row(c, base)["settlement_cadence"] == "daily"
    # Switching to daily never needs a date, and repeating a choice is fine.
    assert set_cadence(c, base, "daily").status_code == 200
    assert set_dates(c, base, TOMORROW).status_code == 200
    assert set_cadence(c, base, "trip").status_code == 200
    assert set_cadence(c, base, "trip").status_code == 200


def test_end_date_cannot_be_removed_while_the_trip_is_settled_as_a_whole(ledger):
    c, base = ledger
    assert set_dates(c, base, TOMORROW, start=YESTERDAY).status_code == 200
    assert set_cadence(c, base, "trip").status_code == 200

    for body in ({}, {"start_date": YESTERDAY, "end_date": None}):
        refused = c.patch(base, headers=auth(), json=body)
        assert refused.status_code == 422
        assert refused.json()["error"]["code"] == "END_DATE_REQUIRED"
    row = trip_row(c, base)
    assert (row["start_date"], row["end_date"]) == (YESTERDAY, TOMORROW)
    # Moving it is allowed; under the daily cadence it can be cleared again.
    assert set_dates(c, base, TODAY.isoformat()).status_code == 200
    assert set_cadence(c, base, "daily").status_code == 200
    assert c.patch(base, headers=auth(), json={}).status_code == 200
    assert trip_row(c, base)["end_date"] is None


def create(c, **fields):
    return c.post(
        "/v1/trips",
        headers=auth(),
        json={"name": "札幌", "default_currency": "JPY", **fields},
    )


def test_cadence_is_chosen_when_the_trip_is_created(ledger):
    c, _ = ledger

    plain = create(c)
    whole = create(
        c, settlement_cadence="trip", start_date=YESTERDAY, end_date=TOMORROW
    )
    dated = create(c, settlement_cadence="daily", end_date=TOMORROW)

    assert plain.status_code == whole.status_code == dated.status_code == 201
    rows = {r["id"]: r for r in c.get("/v1/trips", headers=auth()).json()["data"]}
    assert rows[plain.json()["data"]["id"]]["settlement_cadence"] == "daily"
    assert rows[plain.json()["data"]["id"]]["end_date"] is None
    row = rows[whole.json()["data"]["id"]]
    assert (row["settlement_cadence"], row["start_date"], row["end_date"]) == (
        "trip",
        YESTERDAY,
        TOMORROW,
    )
    assert rows[dated.json()["data"]["id"]]["end_date"] == TOMORROW
    # The response keeps its three fields.
    assert set(whole.json()["data"]) == {"id", "name", "default_currency"}


def test_creating_a_trip_settled_as_a_whole_needs_an_end_date(ledger):
    c, _ = ledger
    count = len(c.get("/v1/trips", headers=auth()).json()["data"])

    missing = create(c, settlement_cadence="trip")

    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "END_DATE_REQUIRED"
    assert create(c, settlement_cadence="weekly").status_code == 422
    assert create(c, start_date=TOMORROW, end_date=YESTERDAY).status_code == 422
    assert len(c.get("/v1/trips", headers=auth()).json()["data"]) == count


def test_replaying_an_offline_create_never_overwrites_the_stored_cadence(ledger):
    c, _ = ledger
    body = {"client_id": str(uuid4()), "settlement_cadence": "daily"}
    first = create(c, **body)
    base = "/v1/trips/" + first.json()["data"]["id"]
    assert set_dates(c, base, TOMORROW).status_code == 200
    assert set_cadence(c, base, "trip").status_code == 200

    again = create(c, **body)

    assert again.status_code == 201
    assert again.json() == first.json()
    assert trip_row(c, base)["settlement_cadence"] == "trip"
    assert trip_row(c, base)["end_date"] == TOMORROW


def test_preview_made_before_a_cadence_switch_cannot_be_confirmed(ledger):
    c, base = ledger
    expense(c, base, day=YESTERDAY)
    stale = preview(c, base)
    assert set_dates(c, base, TOMORROW).status_code == 200
    assert set_cadence(c, base, "trip").status_code == 200

    assert pay(c, base, stale).status_code == 409

    assert c.get(base + "/ledger", headers=auth()).json()["data"]["payments"] == []


def test_settled_day_stays_settled_and_a_waiting_mark_resumes_after_the_switch_back(
    ledger,
):
    c, base = ledger
    expense(c, base, day=LAST_WEEK)
    assert pay(c, base, preview(c, base, [LAST_WEEK])).status_code == 201
    expense(c, base, day=YESTERDAY)
    p = preview(c, base)
    mark = c.post(
        base + "/payments/mark-paid",
        headers=auth("b"),
        json={"dates": p["dates"], "token": p["token"], "currency": "CNY"},
    ).json()["data"]
    assert set_dates(c, base, TOMORROW).status_code == 200

    assert set_cadence(c, base, "trip").status_code == 200

    data = c.get(base + "/ledger", headers=auth()).json()["data"]
    days = {d["date"]: d for d in data["days"]}
    assert days[LAST_WEEK]["status"] == "settled"
    assert len(days[LAST_WEEK]["settled_transfers"]) == 1
    assert days[YESTERDAY]["status"] == "recording"
    assert data["payer_marks"][0]["stale"] is True
    p = c.post(base + "/payments/preview", headers=auth(), json={})
    assert p.status_code == 200 and p.json()["data"]["groups"] == []

    assert set_cadence(c, base, "daily").status_code == 200

    data = c.get(base + "/ledger", headers=auth()).json()["data"]
    assert data["payer_marks"][0]["stale"] is False
    p = preview(c, base)
    confirmed = c.post(
        base + "/payments",
        headers=auth(),
        json={
            "dates": p["dates"],
            "token": p["token"],
            "currency": "CNY",
            "mark_id": mark["id"],
        },
    )
    assert confirmed.status_code == 201, confirmed.text
