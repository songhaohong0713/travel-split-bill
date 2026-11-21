"""The deployed payment projection trigger, run against a real PostgreSQL."""

import json
from pathlib import Path

import pytest
from postgres_scratch import requires_postgres, scratch_database

pytestmark = requires_postgres
MIGRATIONS = Path("cloudbase/migrations")
STUBS = """
CREATE TABLE public.users (id varchar(36) PRIMARY KEY);
CREATE TABLE public.trips (id varchar(36) PRIMARY KEY);
CREATE TABLE public.trip_ledgers (
  trip_id varchar(36) PRIMARY KEY REFERENCES public.trips(id),
  revision integer NOT NULL, state_json jsonb NOT NULL);
INSERT INTO users VALUES ('a'), ('b'), ('c');
INSERT INTO trips VALUES ('t');
"""
TRANSFER = {
    "id": "transfer",
    "format_version": 2,
    "currency": "CNY",
    "amount": "100.00",
    "from_participant_id": "b",
    "to_participant_id": "c",
    "allocations": [{"date": "2026-10-08", "amount": "100.00"}],
    "payer_allocations": [{"date": "2026-10-07", "amount": "-100.00"}],
    "created_by": "c",
    "created_at": "2026-11-21T00:00:00+00:00",
}
OFFSET = {
    "id": "offset",
    "format_version": 2,
    "currency": "CNY",
    "amount": "0.00",
    "from_participant_id": None,
    "to_participant_id": "a",
    "allocations": [
        {"date": "2026-10-07", "amount": "100.00"},
        {"date": "2026-10-08", "amount": "-100.00"},
    ],
    "payer_allocations": [],
    "offset_only": True,
    "created_by": "b",
    "created_at": "2026-11-21T00:00:00+00:00",
}


def projection_ddl():
    """Only the projection tables and trigger of the hardening migration."""
    sql = (MIGRATIONS / "20261024010100_backend_hardening.sql").read_text("utf-8")
    start = sql.index("CREATE TABLE public.payment_records")
    end = sql.index("-- Backfill query projections")
    return sql[start:end]


@pytest.fixture
def psql():
    with scratch_database(STUBS + projection_ddl()) as run:
        yield run


def commit(run, payments, check=True):
    state = json.dumps({"payments": payments}).replace("'", "''")
    return run(
        "INSERT INTO trip_ledgers VALUES ('t', 1, '" + state + "'::jsonb) "
        "ON CONFLICT (trip_id) DO UPDATE SET revision = trip_ledgers.revision + 1,"
        " state_json = EXCLUDED.state_json",
        check=check,
    )


def test_member_format_transfer_needs_no_migration(psql):
    commit(psql, [TRANSFER])

    rows = psql(
        "SELECT from_id, to_id, amount FROM payment_records;"
        "SELECT occurred_at, amount FROM payment_allocations"
    ).stdout.split()
    assert rows == ["b|c|100.00", "2026-10-08|100.00"]


def test_offset_without_payer_is_projected_once_from_id_is_nullable(psql):
    refused = commit(psql, [OFFSET], check=False)
    assert "from_id" in refused.stderr and "null value" in refused.stderr

    psql((MIGRATIONS / "20261119010000_offset_settlement.sql").read_text("utf-8"))
    commit(psql, [TRANSFER, OFFSET])

    rows = psql(
        "SELECT id, coalesce(from_id, 'NULL'), to_id, amount"
        " FROM payment_records ORDER BY id;"
        "SELECT occurred_at, amount FROM payment_allocations"
        " WHERE payment_id = 'offset' ORDER BY 1"
    ).stdout.split()
    assert rows == [
        "offset|NULL|a|0.00",
        "transfer|b|c|100.00",
        "2026-10-07|100.00",
        "2026-10-08|-100.00",
    ]
    # Revoking it later still passes the immutability check.
    revoked = {**OFFSET, "revoked_at": "2026-11-22T00:00:00+00:00", "revoked_by": "a"}
    commit(psql, [TRANSFER, revoked])
    assert psql(
        "SELECT revoked_at IS NOT NULL FROM payment_records WHERE id = 'offset'"
    ).stdout.split() == ["t"]
