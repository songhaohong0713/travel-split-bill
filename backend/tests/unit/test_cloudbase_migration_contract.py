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
    sql = Path("infra/cloudbase/20260919_trip_collaboration.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS trip_members" in sql
    assert "CREATE TABLE IF NOT EXISTS trip_invites" in sql
    assert "FUNCTION public.tsb_create_trip_invite" in sql
    assert "FUNCTION public.tsb_accept_trip_invite" in sql
    assert "INSERT INTO trip_members" in sql