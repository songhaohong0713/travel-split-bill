from pathlib import Path

REQUIRED_RPCS = {
    "tsb_upsert_wechat_user_and_issue_refresh_token",
    "tsb_rotate_refresh_token",
    "tsb_create_trip",
    "tsb_create_expense_with_idempotency",
    "tsb_update_expense_revision",
    "tsb_publish_settlement_version",
    "tsb_create_share_link",
    "tsb_create_receipt_image",
    "tsb_mark_receipt_uploaded",
    "tsb_create_receipt_job",
    "tsb_claim_receipt_job",
}


def test_migration_defines_all_mutation_rpcs():
    sql = Path("infra/cloudbase/cloudbase_http_api_migration.sql").read_text(
        encoding="utf-8"
    )
    for name in REQUIRED_RPCS:
        assert f"FUNCTION public.{name}" in sql
    assert "REVOKE ALL ON FUNCTION" in sql
    assert "GRANT EXECUTE ON FUNCTION" in sql
    assert "service_role" in sql


def test_collaboration_increment_defines_invite_rpcs():
    sql = Path("infra/cloudbase/20260919_trip_collaboration.sql").read_text(
        encoding="utf-8"
    )
    assert "CREATE TABLE IF NOT EXISTS trip_members" in sql
    assert "CREATE TABLE IF NOT EXISTS trip_invites" in sql
    assert "FUNCTION public.tsb_create_trip_invite" in sql
    assert "FUNCTION public.tsb_accept_trip_invite" in sql
    assert "INSERT INTO trip_members" in sql


def test_receipt_job_membership_increment_allows_trip_members():
    sql = Path("infra/cloudbase/20260927_receipt_job_membership.sql").read_text(
        encoding="utf-8"
    )
    assert "tsb_create_receipt_image" in sql
    assert "trip_members" in sql


def test_trip_ledger_follow_up_defines_owner_delete_rpcs():
    sql = Path("infra/cloudbase/20260928_trip_ledger_follow_up.sql").read_text(
        encoding="utf-8"
    )
    assert "FUNCTION public.tsb_delete_owner_expense" in sql
    assert "FUNCTION public.tsb_delete_owner_trip" in sql
    assert "owner_id=p_owner_id" in sql
    assert "GRANT EXECUTE ON FUNCTION" in sql


def test_collaboration_permissions_allow_trip_members_to_update_any_expense():
    sql = Path("infra/cloudbase/20260929_collaboration_permissions.sql").read_text(
        encoding="utf-8"
    )
    update_function = sql.split(
        "CREATE OR REPLACE FUNCTION public.tsb_update_expense_revision", 1
    )[1].split("CREATE OR REPLACE FUNCTION", 1)[0]

    assert "JOIN trip_members m ON m.trip_id=e.trip_id" in update_function
    assert "m.user_id=p_owner_id" in update_function
    assert "e.owner_id=p_owner_id OR t.owner_id=p_owner_id" not in update_function


def test_member_limit_migration_rebuilds_both_invite_rpcs():
    from app.core.limits import MAX_TRIP_MEMBERS

    sql = Path("cloudbase/migrations/20261121010000_trip_member_limit.sql").read_text(
        encoding="utf-8"
    )
    for name in ("tsb_create_trip_invite", "tsb_accept_trip_invite"):
        body = sql.split(f"CREATE OR REPLACE FUNCTION public.{name}", 1)[1].split(
            "CREATE OR REPLACE FUNCTION", 1
        )[0]
        assert f">= {MAX_TRIP_MEMBERS} THEN" in body
        assert ">= 2" not in body
        assert "service_role" in body
        assert f"GRANT EXECUTE ON FUNCTION public.{name}" in sql

    def function_body(name: str) -> str:
        return sql.split(f"FUNCTION public.{name}", 1)[1].split(
            "CREATE OR REPLACE FUNCTION", 1
        )[0]

    create = function_body("tsb_create_trip_invite")
    accept = function_body("tsb_accept_trip_invite")
    assert create.index(
        "FROM trips WHERE id=p_trip_id AND owner_id=p_creator_id FOR UPDATE"
    ) < create.index("UPDATE trip_invites")
    assert "token_hash=p_token_hash FOR UPDATE" not in accept
    trip_lock = accept.index("FROM trips WHERE id=v_invite.trip_id FOR UPDATE")
    invite_lock = accept.index("FROM trip_invites WHERE id=v_invite.id FOR UPDATE")
    assert accept.index("WHERE token_hash=p_token_hash;") < trip_lock < invite_lock
    for column in (
        "v_invite.used_at IS NOT NULL",
        "v_invite.revoked_at IS NOT NULL",
        "v_invite.expires_at <= now()",
    ):
        assert invite_lock < accept.index(column)
    assert "SET used_at" not in accept
    assert "'member_count', 2" not in accept
    readme = Path("cloudbase/migrations/README.md").read_text(encoding="utf-8")
    assert "20261121010000_trip_member_limit.sql" in readme
    assert "same release" in readme
    assert "TSB_STALE_INVITE_SQL" in readme
    for historical in (
        "infra/cloudbase/20260919_trip_collaboration.sql",
        "infra/cloudbase/20260929_collaboration_permissions.sql",
    ):
        assert historical in readme
        old = Path(historical).read_text(encoding="utf-8")
        guard, _, functions = old.partition(
            "CREATE OR REPLACE FUNCTION public.tsb_create_trip_invite"
        )
        assert "TSB_STALE_INVITE_SQL" in guard
        assert f">= {MAX_TRIP_MEMBERS} THEN" in guard
        body = functions.split("CREATE OR REPLACE FUNCTION", 1)[0]
        assert ">= 2 THEN" in body
        assert f">= {MAX_TRIP_MEMBERS} THEN" not in body


def test_settlement_cadence_migration_matches_the_backend_contract():
    from app.core.ledger import (
        CADENCE_DAILY,
        CADENCE_TRIP,
        SNAPSHOT_CADENCE,
        SNAPSHOT_END_DATE,
    )

    sql = Path("cloudbase/migrations/20261122010000_settlement_cadence.sql").read_text(
        encoding="utf-8"
    )
    assert sql.startswith("BEGIN;") and sql.rstrip().endswith("COMMIT;")
    assert (
        f"ADD COLUMN IF NOT EXISTS settlement_cadence text NOT NULL DEFAULT '{CADENCE_DAILY}'"
        in sql
    )
    assert f"settlement_cadence IN ('{CADENCE_DAILY}','{CADENCE_TRIP}')" in sql
    assert f"settlement_cadence <> '{CADENCE_TRIP}' OR end_date IS NOT NULL" in sql
    snapshot = sql.split("FUNCTION public.tsb_ledger_snapshot", 1)[1].split("$$;", 1)[0]
    # The ledger reads these two keys on both data paths.
    assert f"'{SNAPSHOT_CADENCE}', t.settlement_cadence" in snapshot
    assert f"'{SNAPSHOT_END_DATE}'," in snapshot
    for key in (
        "currency",
        "timezone_offset_minutes",
        "members",
        "revision",
        "state",
        "expenses",
    ):
        assert f"'{key}'," in snapshot
    for name in (
        "tsb_create_trip_with_cadence",
        "tsb_set_trip_settlement_cadence",
    ):
        assert f"CREATE FUNCTION public.{name}(" in sql
        assert f"GRANT EXECUTE ON FUNCTION public.{name}(" in sql
        assert f"REVOKE ALL ON FUNCTION public.{name}(" in sql
    for name in ("tsb_update_trip_dates", "tsb_ledger_snapshot"):
        assert f"CREATE OR REPLACE FUNCTION public.{name}(" in sql
    assert sql.count("'end_date_required'") == 3
    # The function the previous backend calls is left exactly as deployed.
    statements = "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    )
    assert "tsb_create_trip_with_timezone" not in statements
    assert sql.count("<> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'") == 4
    readme = Path("cloudbase/migrations/README.md").read_text(encoding="utf-8")
    assert "20261122010000_settlement_cadence.sql" in readme


def test_placeholder_migration_keeps_the_projection_guard_and_the_accept_function():
    sql = Path("cloudbase/migrations/20261123010000_placeholder_members.sql").read_text(
        encoding="utf-8"
    )
    statements = "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    )
    assert sql.startswith("BEGIN;") and sql.rstrip().endswith("COMMIT;")
    assert (
        "ADD COLUMN IF NOT EXISTS is_placeholder boolean NOT NULL DEFAULT false" in sql
    )
    assert "ADD COLUMN IF NOT EXISTS added_by text REFERENCES public.users(id)" in sql
    new = (
        "tsb_add_placeholder_member",
        "tsb_delete_placeholder_member",
        "tsb_claim_check",
        "tsb_claim_snapshot",
        "tsb_claim_placeholder_member",
    )
    for name in new:
        assert f"CREATE FUNCTION public.{name}(" in sql
        assert f"REVOKE ALL ON FUNCTION public.{name}(" in sql
        assert f"GRANT EXECUTE ON FUNCTION public.{name}(" in sql
    assert (
        statements.count("<> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'") == 6
    )
    # Claiming goes its own way: the general guard of settlement history and
    # the function people join through are not redefined, disabled or bypassed.
    for untouched in (
        "tsb_project_ledger_payments",
        "tsb_accept_trip_invite",
        "TRIGGER",
    ):
        assert untouched not in statements
    assert "session_replication_role" not in sql
    claim = statements.split("FUNCTION public.tsb_claim_placeholder_member", 1)[
        1
    ].split("$$;", 1)[0]
    # Settlement rows first, then the ledger state the trigger compares them with.
    assert claim.index("UPDATE payment_records") < claim.index("UPDATE trip_ledgers")
    assert claim.index("UPDATE trip_ledgers") < claim.index("DELETE FROM users")
    assert "IS DISTINCT FROM p_expected" in claim
    create = statements.split("FUNCTION public.tsb_create_trip_invite", 1)[1].split(
        "$$;", 1
    )[0]
    assert (
        ">= 6 AND NOT EXISTS" in create and "owner_id=p_creator_id FOR UPDATE" in create
    )
    readme = Path("cloudbase/migrations/README.md").read_text(encoding="utf-8")
    assert "20261123010000_placeholder_members.sql" in readme
