"""The deployed invite functions, run against a real PostgreSQL."""

import json
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
