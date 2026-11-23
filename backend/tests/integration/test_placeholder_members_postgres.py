"""The placeholder migration and its claim, run against a real PostgreSQL."""

import json
from pathlib import Path

import pytest
from app.core.placeholders import claim, mentions, removal
from postgres_scratch import requires_postgres, scratch_database

pytestmark = requires_postgres
MIGRATIONS = Path("cloudbase/migrations")
AS_SERVICE = 'SET request.jwt.claims = \'{"role":"service_role"}\';'


def deployed(file, start, end):
    sql = (MIGRATIONS / file).read_text("utf-8")
    begin = sql.index(start)
    return sql[begin : sql.index(end, begin)]


# Tables and functions as deployed before this migration.
SETUP = (
    """
CREATE TABLE users (id varchar(36) PRIMARY KEY, wechat_openid_hash text);
CREATE TABLE trips (id varchar(36) PRIMARY KEY,
  owner_id varchar(36) NOT NULL REFERENCES users(id), name text NOT NULL,
  default_currency varchar(3) NOT NULL, start_date date, end_date date,
  timezone_offset_minutes integer, settlement_cadence text NOT NULL DEFAULT 'daily');
CREATE TABLE trip_members (id text PRIMARY KEY, trip_id text NOT NULL REFERENCES trips(id),
  user_id text NOT NULL REFERENCES users(id), nickname text,
  joined_at timestamp NOT NULL DEFAULT now(), UNIQUE(trip_id, user_id));
CREATE TABLE trip_invites (id text PRIMARY KEY, trip_id text NOT NULL REFERENCES trips(id),
  creator_id text NOT NULL REFERENCES users(id), token_hash text NOT NULL UNIQUE,
  expires_at timestamp NOT NULL, used_at timestamp, revoked_at timestamp);
CREATE TABLE trip_ledgers (trip_id varchar(36) PRIMARY KEY REFERENCES trips(id),
  revision integer NOT NULL, state_json jsonb NOT NULL);
CREATE TABLE expenses (id text PRIMARY KEY, trip_id text NOT NULL REFERENCES trips(id),
  owner_id text NOT NULL REFERENCES users(id), revision integer NOT NULL,
  occurred_at date NOT NULL, payload_json json NOT NULL);
"""
    + deployed(
        "20261024010100_backend_hardening.sql",
        "CREATE TABLE public.payment_records",
        "-- Backfill query projections",
    )
    + "ALTER TABLE public.payment_records ALTER COLUMN from_id DROP NOT NULL;"
    + deployed(
        "20261122010000_settlement_cadence.sql",
        "CREATE OR REPLACE FUNCTION public.tsb_ledger_snapshot",
        "REVOKE ALL ON FUNCTION public.tsb_create_trip_with_cadence",
    )
    + deployed(
        "20261121010000_trip_member_limit.sql",
        "CREATE OR REPLACE FUNCTION public.tsb_create_trip_invite",
        "REVOKE ALL ON FUNCTION public.tsb_create_trip_invite",
    )
    + (MIGRATIONS / "20261123010000_placeholder_members.sql").read_text("utf-8")
    + """
INSERT INTO users(id) VALUES ('m'), ('x');
INSERT INTO trips(id, owner_id, name, default_currency) VALUES ('t', 'm', '京都', 'CNY');
INSERT INTO trip_members(id, trip_id, user_id) VALUES ('owner:t', 't', 'm');
INSERT INTO trip_invites VALUES ('i', 't', 'm', 'hash', now() + interval '1 day');
"""
)


@pytest.fixture
def call():
    with scratch_database(SETUP) as run:

        def call(sql, check=True):
            result = run(AS_SERVICE + "SELECT " + sql, check=check)
            return json.loads(result.stdout) if check else result

        call.run = run
        yield call


def literal(value):
    return "'" + json.dumps(value, ensure_ascii=False).replace("'", "''") + "'::jsonb"


def add(call, placeholder="p", name="小王", by="m"):
    return call(
        f"tsb_add_placeholder_member('member:{placeholder}', '{placeholder}', 't',"
        f" '{by}', '{name}')"
    )


def snapshot(call, user="m"):
    return call(f"tsb_ledger_snapshot('t', '{user}')")


def expense(person, payer="m", amount="400"):
    return {
        "participants": ["m", person],
        "expenses": [
            {
                "expense_id": "e",
                "payer_id": payer,
                "settlement_currency": "CNY",
                "items": [
                    {
                        "item_id": "i",
                        "amount": {"currency": "CNY", "amount": amount},
                        "allocation": {"m": "0.5", person: "0.5"},
                    }
                ],
            }
        ],
    }


def settled_pair_trip(call, placeholder="p"):
    """m and a placeholder with one settled day in the two-member format."""
    add(call, placeholder)
    call.run(
        "INSERT INTO expenses VALUES ('e', 't', 'm', 1, '2026-10-07', "
        + literal(expense(placeholder)).replace("::jsonb", "::json")
        + ")"
    )
    signed = "200.00" if "m" < placeholder else "-200.00"
    payment = {
        "id": "paid",
        "currency": "CNY",
        "amount": "200.00",
        "from_participant_id": placeholder,
        "to_participant_id": "m",
        "allocations": [{"date": "2026-10-07", "amount": signed}],
        "freeze_basis": [{"date": "2026-10-07", "currency": "CNY", "amount": signed}],
        "basis_currencies": {"2026-10-07": ["CNY"]},
        "depends_on": [],
        "created_by": "m",
        "created_at": "2026-10-08T00:00:00+00:00",
        "on_behalf": {"by": "m", "placeholder": "payer"},
    }
    call.run(
        "INSERT INTO trip_ledgers VALUES ('t', 1, "
        + literal({"payments": [payment]})
        + ")"
    )
    return payment


def do_claim(call, claimer, placeholder="p", expected=None, token="hash"):
    prepared = call(f"tsb_claim_snapshot('{token}', '{claimer}', '{placeholder}')")
    if "snapshot" not in prepared:
        return prepared
    old = prepared["snapshot"]
    rewrite = claim(old, placeholder, claimer)
    return call(
        f"tsb_claim_placeholder_member('{token}', '{claimer}', '{placeholder}', "
        + ", ".join(
            literal(v)
            for v in (
                expected or old,
                rewrite["expenses"],
                rewrite["state"],
                rewrite["payments"],
            )
        )
        + ")"
    )


def rows(call, sql):
    return call.run(sql).stdout.split("\n")[:-1]


def test_existing_members_are_real_and_placeholders_count_toward_six(call):
    assert rows(
        call, "SELECT user_id, is_placeholder, added_by IS NULL FROM trip_members"
    ) == ["m|f|t"]

    assert add(call, "p") == {"id": "p", "nickname": "小王"}

    assert rows(
        call,
        "SELECT user_id, nickname, is_placeholder, added_by FROM trip_members"
        " WHERE is_placeholder",
    ) == ["p|小王|t|m"]
    assert rows(call, "SELECT wechat_openid_hash IS NULL FROM users WHERE id='p'") == [
        "t"
    ]
    assert add(call, "q", by="stranger") == {"not_found": True}
    for n in "abcd":
        add(call, n)
    assert add(call, "seventh") == {"full": True}
    assert rows(call, "SELECT count(*) FROM trip_members") == ["6"]
    # A full trip with a placeholder still issues a link.
    created = call(
        "tsb_create_trip_invite('i2', 't', 'm', 'hash2', (now() + interval '1 day')::timestamp)"
    )
    assert created["id"] == "i2"


def test_delete_applies_the_backends_decision_only_on_an_unchanged_trip(call):
    add(call, "p")
    listed = {**expense("x"), "participants": ["m", "x", "p"]}
    call.run("INSERT INTO trip_members(id, trip_id, user_id) VALUES ('mx', 't', 'x')")
    call.run(
        "INSERT INTO expenses VALUES ('e', 't', 'm', 1, '2026-10-07', "
        + literal(listed).replace("::jsonb", "::json")
        + ")"
    )
    old = snapshot(call)
    plan = removal(old, "p")
    assert [e["id"] for e in plan["expenses"]] == ["e"]

    def delete(expected, by="m", target="p"):
        return call(
            f"tsb_delete_placeholder_member('t', '{by}', '{target}', "
            f"{literal(expected)}, {literal(plan['expenses'])})"
        )

    assert delete(old, by="stranger") == {"not_found": True}
    assert delete(old, target="x") == {"not_found": True}
    assert delete({**old, "revision": 9}) == {"conflict": True}
    assert rows(call, "SELECT count(*) FROM trip_members") == ["3"]

    assert delete(old) == {"ok": True}

    assert rows(call, "SELECT user_id FROM trip_members ORDER BY 1") == ["m", "x"]
    assert rows(call, "SELECT count(*) FROM users WHERE id='p'") == ["0"]
    assert rows(
        call, "SELECT revision, payload_json->'participants' FROM expenses"
    ) == ['2|["m", "x"]']
    assert delete(snapshot(call)) == {"not_found": True}


@pytest.mark.parametrize(
    "placeholder,claimer", [("a", "b"), ("a", "z"), ("z", "y"), ("z", "b")]
)
def test_claim_rewrites_one_trip_whichever_way_the_ids_sort(call, placeholder, claimer):
    stored = settled_pair_trip(call, placeholder)
    before = snapshot(call)

    result = do_claim(call, claimer, placeholder)

    assert result == {
        "id": "t",
        "name": "京都",
        "default_currency": "CNY",
        "member_count": 2,
    }
    after = snapshot(call, claimer)
    assert after["members"] == sorted(["m", claimer])
    assert after["revision"] == before["revision"] + 1
    assert not mentions(after, placeholder)
    assert after["expenses"][0]["revision"] == 2
    paid = after["state"]["payments"][0]
    assert (paid["from_participant_id"], paid["to_participant_id"], paid["amount"]) == (
        claimer,
        "m",
        "200.00",
    )
    assert paid["on_behalf"] == stored["on_behalf"]
    signed = "200.00" if "m" < claimer else "-200.00"
    assert paid["allocations"] == [{"date": "2026-10-07", "amount": signed}]
    assert rows(
        call,
        "SELECT from_id, to_id, amount, payload_json->>'from_participant_id'"
        " FROM payment_records",
    ) == [f"{claimer}|m|200.00|{claimer}"]
    assert rows(call, "SELECT amount FROM payment_allocations") == [signed]
    assert rows(
        call,
        "SELECT user_id, nickname, is_placeholder, id FROM trip_members ORDER BY joined_at",
    ) == ["m||f|owner:t", f"{claimer}|小王|f|member:{placeholder}"]
    assert rows(call, f"SELECT count(*) FROM users WHERE id='{placeholder}'") == ["0"]
    # The untouched trigger still guards this history after the claim.
    tampered = json.loads(json.dumps(after["state"]))
    tampered["payments"][0]["amount"] = "1.00"
    refused = call.run(
        "UPDATE trip_ledgers SET state_json=" + literal(tampered), check=False
    )
    assert "TSB_IMMUTABLE_PAYMENT" in refused.stderr
    revoked = json.loads(json.dumps(after["state"]))
    revoked["payments"][0]["revoked_at"] = "2026-11-23T00:00:00+00:00"
    call.run("UPDATE trip_ledgers SET state_json=" + literal(revoked))


def test_claim_refusals(call):
    settled_pair_trip(call)

    assert do_claim(call, "m") == {"already_member": True}
    assert do_claim(call, "r", "m") == {"unavailable": True}
    assert do_claim(call, "r", "gone") == {"unavailable": True}
    assert do_claim(call, "r", token="other") == {"expired": True}
    stale = {**snapshot(call), "revision": 99}
    assert do_claim(call, "r", expected=stale) == {"conflict": True}
    call.run("UPDATE trip_invites SET revoked_at = now()")
    assert do_claim(call, "r") == {"expired": True}
    assert rows(call, "SELECT user_id FROM trip_members ORDER BY 1") == ["m", "p"]


def test_claim_that_fails_at_its_last_statement_leaves_nothing_behind(call):
    settled_pair_trip(call)
    # Another row still names the placeholder, so deleting its user row fails.
    call.run(
        "INSERT INTO trips(id, owner_id, name, default_currency) VALUES ('u', 'x', '别的', 'CNY');"
        "INSERT INTO trip_members(id, trip_id, user_id) VALUES ('stray', 'u', 'p')"
    )
    probe = (
        "SELECT (SELECT string_agg(user_id || is_placeholder::text, ',' ORDER BY id) FROM trip_members),"
        " (SELECT md5(string_agg(payload_json::text || revision::text, '')) FROM expenses),"
        " (SELECT md5(string_agg(coalesce(from_id,'') || to_id || payload_json::text, '')) FROM payment_records),"
        " (SELECT md5(state_json::text || revision::text) FROM trip_ledgers),"
        " (SELECT string_agg(amount::text, ',') FROM payment_allocations),"
        " (SELECT string_agg(id, ',' ORDER BY id) FROM users)"
    )
    before = rows(call, probe)
    old = snapshot(call)
    rewrite = claim(old, "p", "r")

    failed = call(
        "tsb_claim_placeholder_member('hash', 'r', 'p', "
        + ", ".join(
            literal(v)
            for v in (old, rewrite["expenses"], rewrite["state"], rewrite["payments"])
        )
        + ")",
        check=False,
    )

    assert failed.returncode != 0
    assert "foreign key" in failed.stderr
    assert rows(call, probe) == before
    assert snapshot(call) == old


def test_only_the_service_role_may_call_the_new_functions(call):
    for sql in (
        "tsb_add_placeholder_member('i', 'p', 't', 'm', 'n')",
        "tsb_delete_placeholder_member('t', 'm', 'p', '{}', '[]')",
        "tsb_claim_check('hash', 'r', 'p')",
        "tsb_claim_snapshot('hash', 'r', 'p')",
        "tsb_claim_placeholder_member('hash', 'r', 'p', '{}', '[]', '{}', '[]')",
    ):
        assert "TSB_FORBIDDEN" in call.run("SELECT " + sql, check=False).stderr
