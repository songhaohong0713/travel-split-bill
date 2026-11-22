"""The settlement cadence migration, run against a real PostgreSQL."""

import json
from pathlib import Path

import pytest
from postgres_scratch import requires_postgres, scratch_database

pytestmark = requires_postgres
MIGRATIONS = Path("cloudbase/migrations")
AS_SERVICE = 'SET request.jwt.claims = \'{"role":"service_role"}\';'


def deployed(file, start, end):
    sql = (MIGRATIONS / file).read_text("utf-8")
    begin = sql.index(start)
    return sql[begin : sql.index(end, begin)]


# The trips-related schema as deployed before this migration.
SETUP = (
    """
CREATE TABLE users (id varchar(36) PRIMARY KEY, wechat_openid_hash text);
CREATE TABLE trips (id varchar(36) PRIMARY KEY,
  owner_id varchar(36) NOT NULL REFERENCES users(id), name text NOT NULL,
  default_currency varchar(3) NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  start_date date, end_date date, timezone_offset_minutes integer,
  CONSTRAINT trip_date_order CHECK (start_date IS NULL OR end_date IS NULL OR start_date <= end_date));
CREATE TABLE trip_members (id text PRIMARY KEY, trip_id text NOT NULL REFERENCES trips(id),
  user_id text NOT NULL REFERENCES users(id), UNIQUE(trip_id, user_id));
CREATE TABLE trip_ledgers (trip_id varchar(36) PRIMARY KEY REFERENCES trips(id),
  revision integer NOT NULL, state_json jsonb NOT NULL);
CREATE TABLE expenses (id text PRIMARY KEY, trip_id text NOT NULL REFERENCES trips(id),
  revision integer NOT NULL, occurred_at date NOT NULL, payload_json jsonb NOT NULL);
"""
    + deployed(
        "20261101030000_calendar_orphans.sql",
        "CREATE OR REPLACE FUNCTION public.tsb_create_trip_with_timezone",
        "CREATE FUNCTION public.tsb_set_trip_timezone",
    )
    + deployed(
        "20261024010000_daily_settlement.sql",
        "CREATE FUNCTION public.tsb_commit_ledger",
        "REVOKE ALL ON FUNCTION public.tsb_lock_expense_trip()",
    )
    + deployed(
        "20261024010000_daily_settlement.sql",
        "CREATE FUNCTION public.tsb_update_trip_dates",
        "REVOKE ALL ON FUNCTION public.tsb_update_trip_dates",
    )
    + deployed(
        "20261024010100_backend_hardening.sql",
        "CREATE FUNCTION public.tsb_member_ledger_snapshots",
        "REVOKE ALL ON FUNCTION public.tsb_member_ledger_snapshots",
    )
    + AS_SERVICE
    + """
SELECT tsb_create_trip_with_timezone('old', 'a', '京都', 'CNY', -540);
UPDATE trips SET start_date = '2026-10-01', end_date = '2026-10-05' WHERE id = 'old';
"""
)


@pytest.fixture
def call():
    with scratch_database(SETUP) as run:

        def call(sql, check=True):
            result = run(AS_SERVICE + "SELECT " + sql, check=check)
            return json.loads(result.stdout) if check else result

        call.run = run
        call.before = call("tsb_ledger_snapshot('old', 'a')")
        run((MIGRATIONS / "20261122010000_settlement_cadence.sql").read_text("utf-8"))
        yield call


def row(call, trip):
    return call.run(
        "SELECT settlement_cadence, coalesce(start_date::text, '-'),"
        f" coalesce(end_date::text, '-') FROM trips WHERE id = '{trip}'"
    ).stdout.strip()


def create(
    call, trip, cadence="daily", start="NULL", end="NULL", owner="a", name="札幌"
):
    return call(
        f"tsb_create_trip_with_cadence('{trip}', '{owner}', '{name}', 'JPY', -540,"
        f" '{cadence}', {start}, {end})"
    )


def test_existing_trips_become_daily_and_their_snapshot_only_gains_two_keys(call):
    assert row(call, "old") == "daily|2026-10-01|2026-10-05"

    after = call("tsb_ledger_snapshot('old', 'a')")

    assert after == {
        **call.before,
        "settlement_cadence": "daily",
        "settlement_end_date": None,
    }
    home = call("(SELECT jsonb_agg(row) FROM tsb_member_ledger_snapshots('a') row)")
    assert home == [{"trip_id": "old", **after}]
    assert call("tsb_ledger_snapshot('old', 'stranger')") == {"not_found": True}


def test_commit_still_matches_a_snapshot_read_by_any_backend(call):
    snapshot = call("tsb_ledger_snapshot('old', 'a')")
    state = json.dumps({"payments": []})

    def commit(expected):
        literal = json.dumps(expected).replace("'", "''")
        return call(
            f"tsb_commit_ledger('old', 'a', '{literal}'::jsonb, '{state}'::jsonb)"
        )

    # A preview taken before the migration no longer matches; a fresh read does.
    assert commit(call.before) == {"conflict": True}
    assert commit(snapshot) == {"ok": True}


def test_snapshot_carries_the_end_date_only_under_the_trip_cadence(call):
    create(call, "t", "trip", "'2026-11-20'", "'2026-11-30'")

    whole = call("tsb_ledger_snapshot('t', 'a')")
    call("tsb_set_trip_settlement_cadence('t', 'a', 'daily')")
    daily = call("tsb_ledger_snapshot('t', 'a')")

    assert (whole["settlement_cadence"], whole["settlement_end_date"]) == (
        "trip",
        "2026-11-30",
    )
    assert (daily["settlement_cadence"], daily["settlement_end_date"]) == (
        "daily",
        None,
    )
    # Editing dates of a daily trip therefore leaves its previews valid.
    call("tsb_update_trip_dates('t', 'a', NULL, '2026-12-05')")
    assert call("tsb_ledger_snapshot('t', 'a')") == daily


def test_create_stores_the_cadence_and_replays_return_the_stored_trip(call):
    created = create(call, "t", "trip", "'2026-11-20'", "'2026-11-30'")

    assert created == {
        "id": "t",
        "name": "札幌",
        "default_currency": "JPY",
        "member_count": 1,
    }
    assert row(call, "t") == "trip|2026-11-20|2026-11-30"
    assert create(call, "plain")["member_count"] == 1
    assert row(call, "plain") == "daily|-|-"
    # A replay with other settings neither fails nor overwrites.
    assert create(call, "t", "daily") == created
    assert row(call, "t") == "trip|2026-11-20|2026-11-30"
    assert create(call, "t", name="别的名字") == {"conflict": True}
    assert create(call, "t", owner="b") == {"conflict": True}
    assert create(call, "bad", "trip") == {"end_date_required": True}
    assert row(call, "bad") == ""
    refused = call(
        "tsb_create_trip_with_cadence('w', 'a', '札幌', 'JPY', -540, 'weekly', NULL, NULL)",
        check=False,
    )
    assert "TSB_INVALID_CADENCE" in refused.stderr


def test_function_of_the_previous_backend_keeps_creating_daily_trips(call):
    created = call("tsb_create_trip_with_timezone('legacy', 'a', '大阪', 'JPY', -540)")

    assert created["id"] == "legacy"
    assert row(call, "legacy") == "daily|-|-"


def test_only_the_creator_switches_and_trip_needs_an_end_date(call):
    create(call, "t")
    call.run(
        "INSERT INTO users(id) VALUES ('b');"
        "INSERT INTO trip_members VALUES ('m', 't', 'b')"
    )
    name = "tsb_set_trip_settlement_cadence"

    assert call(f"{name}('t', 'b', 'trip')") == {"not_found": True}
    assert call(f"{name}('missing', 'a', 'trip')") == {"not_found": True}
    assert call(f"{name}('t', 'a', 'trip')") == {"end_date_required": True}
    assert row(call, "t") == "daily|-|-"
    assert call(f"{name}('t', 'a', 'daily')") == {"ok": True}
    call("tsb_update_trip_dates('t', 'a', NULL, '2026-11-30')")
    assert call(f"{name}('t', 'a', 'trip')") == {"ok": True}
    assert call(f"{name}('t', 'a', 'trip')") == {"ok": True}
    assert row(call, "t") == "trip|-|2026-11-30"
    assert (
        "TSB_INVALID_CADENCE" in call(f"{name}('t', 'a', 'weekly')", check=False).stderr
    )


def test_dates_cannot_drop_the_end_date_of_a_whole_trip(call):
    create(call, "t", "trip", "'2026-11-20'", "'2026-11-30'")
    dates = "tsb_update_trip_dates"

    assert call(f"{dates}('t', 'a', '2026-11-20', NULL)") == {"end_date_required": True}
    assert call(f"{dates}('t', 'a', NULL, NULL)") == {"end_date_required": True}
    assert row(call, "t") == "trip|2026-11-20|2026-11-30"
    assert call(f"{dates}('t', 'a', NULL, '2026-12-02')") == {"ok": True}
    assert call(f"{dates}('t', 'b', NULL, '2026-12-02')") == {"not_found": True}
    call("tsb_set_trip_settlement_cadence('t', 'a', 'daily')")
    assert call(f"{dates}('t', 'a', NULL, NULL)") == {"ok": True}
    assert row(call, "t") == "daily|-|-"


def test_table_refuses_rows_no_function_would_write(call):
    create(call, "t", "trip", "NULL", "'2026-11-30'")

    orphan = call.run("UPDATE trips SET end_date = NULL WHERE id = 't'", check=False)
    unknown = call.run(
        "UPDATE trips SET settlement_cadence = 'weekly' WHERE id = 't'", check=False
    )

    assert "trip_cadence_needs_end_date" in orphan.stderr
    assert "trip_settlement_cadence_values" in unknown.stderr


def test_only_the_service_role_may_call_the_new_functions(call):
    for sql in (
        "tsb_create_trip_with_cadence('x', 'a', 'n', 'JPY', 0, 'daily', NULL, NULL)",
        "tsb_set_trip_settlement_cadence('old', 'a', 'daily')",
        "tsb_update_trip_dates('old', 'a', NULL, NULL)",
        "tsb_ledger_snapshot('old', 'a')",
    ):
        assert "TSB_FORBIDDEN" in call.run("SELECT " + sql, check=False).stderr
