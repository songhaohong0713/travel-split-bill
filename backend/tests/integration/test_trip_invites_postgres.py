"""The deployed invite functions, run against a real PostgreSQL."""

import json
import subprocess
import time
from pathlib import Path

import pytest
from postgres_scratch import requires_postgres, scratch_database

pytestmark = requires_postgres
SETUP = (
    """
CREATE TABLE users (id text PRIMARY KEY, wechat_openid_hash text);
CREATE TABLE trips (id text PRIMARY KEY, owner_id text NOT NULL REFERENCES users(id),
  name text NOT NULL, default_currency text NOT NULL);
"""
    + Path("infra/cloudbase/20260919_trip_collaboration.sql")
    .read_text("utf-8")
    .split("INSERT INTO trip_members(id, trip_id, user_id)\nSELECT", 1)[0]
    .replace("BEGIN;", "", 1)
    + """
INSERT INTO users(id) VALUES ('a');
INSERT INTO trips VALUES ('t', 'a', '京都', 'CNY');
INSERT INTO trip_members(id, trip_id, user_id) VALUES ('owner:t', 't', 'a');
"""
)
AS_SERVICE = 'SET request.jwt.claims = \'{"role":"service_role"}\';'


@pytest.fixture
def call():
    with scratch_database(SETUP) as run:
        run(
            Path("cloudbase/migrations/20261121010000_trip_member_limit.sql").read_text(
                "utf-8"
            )
        )

        def call(sql, check=True):
            result = run(AS_SERVICE + "SELECT " + sql, check=check)
            return json.loads(result.stdout) if check else result

        call.run = run
        call.database_url = run.url
        yield call


def create(call, invite, user="a"):
    return call(
        f"tsb_create_trip_invite('{invite}', 't', '{user}', 'hash-{invite}',"
        " (now() + interval '7 days')::timestamp)"
    )


def accept(call, invite, user):
    return call(f"tsb_accept_trip_invite('hash-{invite}', '{user}')")


def test_one_invite_admits_members_up_to_six(call):
    assert create(call, "i1")["id"] == "i1"

    joined = [accept(call, "i1", user) for user in "bcdef"]

    assert [j["member_count"] for j in joined] == [2, 3, 4, 5, 6]
    assert joined[0] | {"member_count": 0} == {
        "id": "t",
        "name": "京都",
        "default_currency": "CNY",
        "member_count": 0,
    }
    assert accept(call, "i1", "b") == {"already_member": True}
    assert accept(call, "i1", "g") == {"full": True}
    assert create(call, "i2") == {"full": True}
    assert call.run("SELECT count(*) FROM trip_members").stdout.strip() == "6"
    assert (
        call.run(
            "SELECT count(*) FROM trip_invites WHERE used_at IS NOT NULL"
        ).stdout.strip()
        == "0"
    )


def test_replaced_expired_and_formerly_used_invites_are_refused(call):
    create(call, "old")
    assert accept(call, "old", "b")["member_count"] == 2
    assert create(call, "x", user="b") == {"not_found": True}

    create(call, "new")

    assert accept(call, "old", "c") == {"expired": True}
    call.run(
        "UPDATE trip_invites SET expires_at = now() - interval '1 second' WHERE id='new'"
    )
    assert accept(call, "new", "c") == {"expired": True}
    create(call, "used")
    call.run("UPDATE trip_invites SET used_at = now() WHERE id='used'")
    assert accept(call, "used", "c") == {"expired": True}
    assert accept(call, "missing", "c") == {"expired": True}


def test_only_the_service_role_may_call_the_functions(call):
    refused = call.run("SELECT tsb_accept_trip_invite('hash-i1', 'b')", check=False)
    assert "TSB_FORBIDDEN" in refused.stderr


def _start(url, sql):
    process = subprocess.Popen(
        ["psql", url, "-v", "ON_ERROR_STOP=1", "-Atq"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdin is not None
    process.stdin.write(sql)
    process.stdin.close()
    return process


def _wait_until(call, sql, expected, timeout=5):
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        last = call.run(sql).stdout.strip()
        if last == expected:
            return
        time.sleep(0.05)
    raise AssertionError(f"expected {expected!r} from {sql}, last was {last!r}")


def _hold_trip(call, after_wait):
    """Lock trip t, wait until tsb_test_gate is released, then run after_wait."""
    call.run(
        "CREATE TABLE IF NOT EXISTS tsb_test_gate(id int primary key, release boolean);"
        "INSERT INTO tsb_test_gate VALUES (1, false) ON CONFLICT (id) DO UPDATE"
        " SET release=false;"
    )
    return _start(
        call.database_url,
        f"""
BEGIN;
SELECT id FROM trips WHERE id='t' FOR UPDATE;
DO $$
DECLARE n int := 0;
BEGIN
  LOOP
    n := n + 1;
    IF n > 400 THEN RAISE EXCEPTION 'holder timed out'; END IF;
    PERFORM pg_sleep(0.05);
    EXIT WHEN (SELECT release FROM tsb_test_gate WHERE id=1);
  END LOOP;
END $$;
{after_wait}
""",
    )


def _release_holder(call, holder, follower_sql):
    _wait_until(
        call,
        "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()"
        " AND pid<>pg_backend_pid() AND state='active' AND query LIKE '%pg_sleep%'",
        "1",
    )
    follower = _start(
        call.database_url, AS_SERVICE + "SET lock_timeout='12s'; " + follower_sql
    )
    try:
        _wait_until(
            call,
            "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()"
            " AND pid<>pg_backend_pid() AND wait_event_type='Lock'"
            " AND query LIKE '%trip_invite%'",
            "1",
            timeout=10,
        )
        call.run("UPDATE tsb_test_gate SET release=true WHERE id=1")
        # stdin is already closed, so communicate() cannot be used.
        assert follower.wait(timeout=15) == 0, follower.stderr.read()
        assert holder.wait(timeout=15) == 0, holder.stderr.read()
        assert follower.stdout is not None
        return follower.stdout.read()
    except Exception:
        follower.kill()
        holder.kill()
        raise


def test_join_blocked_on_the_trip_sees_a_revoke_committed_while_it_waited(call):
    assert create(call, "i1")["id"] == "i1"
    holder = _hold_trip(
        call, "UPDATE trip_invites SET revoked_at=now() WHERE id='i1'; COMMIT;"
    )
    out = _release_holder(
        call, holder, "SELECT tsb_accept_trip_invite('hash-i1', 'c');"
    )
    assert json.loads(out) == {"expired": True}
    assert call.run("SELECT count(*) FROM trip_members").stdout.strip() == "1"


def test_replacing_a_link_waits_for_the_trip_lock(call):
    assert create(call, "old")["id"] == "old"
    holder = _hold_trip(call, "COMMIT;")
    out = _release_holder(
        call,
        holder,
        "SELECT tsb_create_trip_invite('fresh', 't', 'a', 'hash-fresh',"
        " (now() + interval '7 days')::timestamp);",
    )
    assert json.loads(out)["id"] == "fresh"
    assert (
        call.run(
            "SELECT count(*) FROM trip_invites WHERE id='old' AND revoked_at IS NOT NULL"
        ).stdout.strip()
        == "1"
    )


def test_historical_invite_sql_cannot_replace_the_six_member_functions(call):
    assert create(call, "i1")["id"] == "i1"
    for name in (
        "infra/cloudbase/20260919_trip_collaboration.sql",
        "infra/cloudbase/20260929_collaboration_permissions.sql",
    ):
        replayed = call.run(Path(name).read_text(encoding="utf-8"), check=False)
        assert replayed.returncode != 0
        assert "TSB_STALE_INVITE_SQL" in replayed.stderr
    assert accept(call, "i1", "b")["member_count"] == 2
    assert accept(call, "i1", "c")["member_count"] == 3
    assert create(call, "later")["id"] == "later"
