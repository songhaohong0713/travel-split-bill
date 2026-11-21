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
together with the matching backend, and not before the multi-member settlement
backend is live: until then a three-member trip cannot be previewed or settled.
It rebuilds `tsb_create_trip_invite` and `tsb_accept_trip_invite` with
`CREATE OR REPLACE`; their previous definitions live in `infra/cloudbase`
(`20260919_trip_collaboration.sql`, `20260929_collaboration_permissions.sql`),
and this file is now the current one. The limit rises from 2 to 6, one invite
link admits several members until it expires, is replaced or the trip is full,
and accepting returns the real member count. Links already used under the
single-use rule stay expired. No table or data changes.
