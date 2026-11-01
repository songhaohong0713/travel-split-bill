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
