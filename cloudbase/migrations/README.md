# Canonical CloudBase PostgreSQL incremental migrations

This directory is the single source for current incremental migrations. Apply
unapplied versions in filename order using the existing deployment migration
history. Never replay all versions against an already initialized environment.

`infra/cloudbase` retains the pre-versioned bootstrap schema and legacy RPC
definitions needed to restore pre-upgrade backups in isolated tests. It is not
a second incremental deployment source. Four identical copies formerly named
20261021_daily_settlement, 20261022_backend_hardening,
20261022_business_backup and 20261027_v2_offline_trip have been removed; their
canonical versions here retain the existing deployed filenames and SQL.

For the 2026-11-01 fixes, apply `20261101020000_trip_nickname.sql` and then
`20261101030000_calendar_orphans.sql` before deploying the matching backend.
They add nullable trip nicknames, cleanup attempt counts, fixed trip UTC offsets,
conservative receipt retention timestamps and service-only operational RPCs.
Old trips stay without an offset until their creator sets it; old images begin
a fresh 30-day retention window. No historic payment record is rewritten.

For multi-member settlement, apply `20261119010000_offset_settlement.sql` before
deploying the matching backend. It only makes `payment_records.from_id`
nullable, so that an offset (one member's days cancelling out, amount 0, no
payer) can be projected. Existing rows and the projection trigger are unchanged.

For trips of up to six members, apply `20261121010000_trip_member_limit.sql`
in the same release as the matching backend, and not before the multi-member
settlement backend is live: until then a three-member trip cannot be previewed
or settled. It rebuilds `tsb_create_trip_invite` and `tsb_accept_trip_invite`
with `CREATE OR REPLACE`. The limit rises from 2 to 6, one invite link admits
several members until it expires, is replaced or the trip is full, and
accepting returns the real member count. Links already used under the
single-use rule stay expired. No table or data changes.

The number of members is enforced only inside those two functions. The backend
reports every `full` result as 「这段旅行已满 6 人」. Deploying the backend while
the live functions are still the previous ones rejects the third person and
describes that rejection as a trip that already holds 6. `6` lives in
`backend/app/core/limits.py` as `MAX_TRIP_MEMBERS`; both functions repeat it,
and `backend/tests/unit/test_cloudbase_migration_contract.py` checks the copies.

Do not execute `infra/cloudbase/20260919_trip_collaboration.sql` or
`infra/cloudbase/20260929_collaboration_permissions.sql` after this migration.
Those files still `CREATE OR REPLACE` the 2-person functions. Replaying either
one once this migration is applied aborts with `TSB_STALE_INVITE_SQL` and
leaves the current functions in place. An isolated restore may run them only
against an empty database, and only before this migration.

For the per-trip settlement cadence, apply
`20261122010000_settlement_cadence.sql` before deploying the matching backend.
It adds `trips.settlement_cadence` (`daily` or `trip`, every existing trip
`daily`), two CHECK constraints, the new functions
`tsb_create_trip_with_cadence` and `tsb_set_trip_settlement_cadence`, and
rebuilds `tsb_update_trip_dates` and `tsb_ledger_snapshot` under their existing
signatures. No stored row changes apart from the new column's default.

The previous backend keeps working on the migrated database: it still calls
`tsb_create_trip_with_timezone`, which is left as deployed and creates `daily`
trips, and it ignores the two keys the snapshot gains (`settlement_cadence`,
`settlement_end_date`). The matching backend does not work on an unmigrated
database: the trip list, trip creation and the cadence setting answer 503. So
the order is migration, then backend. Before deploying the backend, confirm the
gateway serves the new objects: read `settlement_cadence` from `trips`, and
call `tsb_ledger_snapshot` once to see the two new keys.

Applying the migration invalidates every settlement preview that is open at
that moment, once: confirming it answers "账单已变化", and previewing again
works. Take backups afterwards: an archive exported before this migration is
refused by a migrated database (`SCHEMA_MISMATCH`), as is a trip-merge plan
prepared from an earlier export.

Once any trip has been switched to `trip`, do not roll the backend back. The
previous backend ignores the cadence, treats that trip as `daily`, and lets its
past days be settled before the trip has ended.
