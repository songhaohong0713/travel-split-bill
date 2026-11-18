"""Offline review package only. No network, database writes, or restore SQL."""
import argparse
from collections import defaultdict
from copy import deepcopy
from datetime import date
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
import re

LIMIT = 50_000_000
SQL_TABLES = ('users','trips','trip_members','trip_invites','expenses','receipt_images','receipt_jobs','settlement_versions','share_links','idempotency_keys','refresh_tokens','trip_ledgers','payment_records','payment_allocations')

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()

def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()

def load(path):
    with open(path, "rb") as stream:
        zipped = stream.read(2) == b"\x1f\x8b"
    with (gzip.open(path, "rb") if zipped else open(path, "rb")) as stream:
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError("INPUT_TOO_LARGE")
    source = json.loads(raw)
    if source.get("format") == "travel-split-backup-v1":
        if digest(source["payload"]) != source["sha256"]:
            raise ValueError("CHECKSUM_MISMATCH")
        return source["payload"]
    if source.get("format") not in (None, "travel-split-preupgrade-v1"):
        raise ValueError("UNSUPPORTED_FORMAT")
    return source

def validate(source):
    tables = source["tables"]
    if not all(isinstance(rows, list) and all(isinstance(r, dict) for r in rows) for rows in tables.values()):
        raise ValueError("INVALID_TABLES")
    trip_ids = {r["id"] for r in tables["trips"]}
    if len(trip_ids) != len(tables["trips"]):
        raise ValueError("DUPLICATE_TRIP")
    for rows in tables.values():
        for row in rows:
            if "trip_id" in row and row["trip_id"] not in trip_ids:
                raise ValueError("ORPHAN_TRIP_REFERENCE")
    for table in ("trip_members", "expenses"):
        if table not in tables:
            raise ValueError("MISSING_REQUIRED_TABLE")

def merged_state(tables, ids):
    state = {"payments": [], "closed_dates": []}
    seen = {}
    for ledger in tables.get("trip_ledgers", []):
        if ledger["trip_id"] not in ids:
            continue
        for key, value in ledger["state_json"].items():
            if key == "payments":
                for payment in value:
                    pid = payment["id"]
                    if pid in seen and seen[pid] != payment:
                        raise ValueError("CONFLICTING_PAYMENT")
                    if pid not in seen:
                        seen[pid] = payment
                        state["payments"].append(deepcopy(payment))
            elif key == "closed_dates":
                state[key] = sorted(set(state[key]) | set(value))
            elif key == "history_confirmation":
                continue  # Exact prior attestations stay in original source archive.
            elif key not in state:
                state[key] = deepcopy(value)
            elif isinstance(value, dict) and isinstance(state[key], dict):
                for item, entry in value.items():
                    if item in state[key] and state[key][item] != entry:
                        raise ValueError("CONFLICTING_LEDGER_STATE")
                    state[key][item] = deepcopy(entry)
            elif state[key] != value:
                raise ValueError("CONFLICTING_LEDGER_STATE")
    state["history_confirmation"] = {"status": "pending", "reason": "offline_trip_merge_requires_payment_reconciliation"}
    return state

def preview(source):
    validate(source)
    tables = source["tables"]
    buckets = defaultdict(list)
    blocked = []
    for trip in sorted(tables["trips"], key=lambda r: r["id"]):
        tid = trip["id"]
        place = re.sub(r"^(?:\d{8}|\d{4})[\s_\-]*", "", trip["name"].strip()).strip()
        members = tuple(sorted({r["user_id"] for r in tables["trip_members"] if r["trip_id"] == tid}))
        rows = [r for r in tables["expenses"] if r["trip_id"] == tid]
        try:
            days = sorted({date.fromisoformat(r["occurred_at"]) for r in rows})
            if not days or len(members) != 2 or not place or trip.get("owner_id") not in members:
                raise ValueError()
            if (days[-1] - days[0]).days + 1 != len(days):
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            blocked.append({"trip_ids": [tid], "reason": "UNKNOWN_OR_NONCONTIGUOUS_DATES_OR_MEMBERS"})
            continue
        buckets[(place, members, trip["default_currency"], trip.get("owner_id"))].append((days[0], days[-1], tid))
    groups = []
    for key, entries in sorted(buckets.items()):
        chunks = []
        for start, end, tid in sorted(entries):
            if not chunks or (start - chunks[-1]["end"]).days > 1:
                chunks.append({"end": end, "ids": [tid]})
            else:
                chunks[-1]["end"] = max(end, chunks[-1]["end"])
                chunks[-1]["ids"].append(tid)
        for chunk in chunks:
            ids = sorted(chunk["ids"])
            if len(ids) < 2:
                continue
            try:
                merged_state(tables, ids)
            except ValueError as error:
                blocked.append({"trip_ids": ids, "reason": str(error)})
                continue
            groups.append({"target": ids[0], "source_ids": ids, "place": key[0], "members": list(key[1]), "currency": key[2]})
    plan = {"format": "trip-merge-plan-v1", "source_sha256": digest(source), "groups": groups, "blocked": blocked}
    return {**plan, "digest": digest(plan)}

def apply(source, plan, confirmation):
    expected = preview(source)
    if confirmation != expected["digest"] or plan != expected:
        raise ValueError("PLAN_OR_SOURCE_CHANGED")
    tables = deepcopy(source["tables"])
    aliases = {tid: g["target"] for g in plan["groups"] for tid in g["source_ids"]}
    for rows in tables.values():
        for row in rows:
            if "trip_id" in row:
                row["trip_id"] = aliases.get(row["trip_id"], row["trip_id"])
    tables["trips"] = [r for r in tables["trips"] if aliases.get(r["id"], r["id"]) == r["id"]]
    for group in plan["groups"]:
        target = group["target"]
        trip = next(t for t in tables["trips"] if t["id"] == target)
        trip["name"] = group["place"]
        dates = sorted(r["occurred_at"] for r in tables["expenses"] if r["trip_id"] == target)
        trip.update(start_date=dates[0], end_date=dates[-1], latest_version_id=None)
        tables["trip_ledgers"] = [r for r in tables.get("trip_ledgers", []) if r["trip_id"] != target]
        tables["trip_ledgers"].append({"trip_id": target, "revision": 0, "state_json": merged_state(source["tables"], group["source_ids"])})
    seen = set()
    members = []
    # Keep the target's existing membership IDs/dates, matching SQL's deduplication.
    original_members = {r['id']:r for r in source['tables']['trip_members']}
    for row in sorted(tables["trip_members"], key=lambda r: (original_members[r['id']]['trip_id'] != r['trip_id'], r['id'])):
        key = (row["trip_id"], row["user_id"])
        if key not in seen:
            members.append(row)
            seen.add(key)
    tables["trip_members"] = members
    validate({"tables": tables})
    return {"format": "trip-merge-review-package-v1", "import_ready": False, "plan": plan, "aliases": aliases, "original": deepcopy(source), "tables": tables}

def write_new(path, value):
    with open(path, "xb") as stream:
        stream.write(canonical(value))

def sql_json(value):
    # Hex data cannot escape the SQL literal, even for hostile exported strings.
    return "convert_from(decode('" + canonical(value).hex() + "','hex'),'UTF8')::jsonb"

def prepare_sql(source, plan, confirmation):
    result = apply(source, plan, confirmation)
    tables = source['tables']
    if set(tables) != set(SQL_TABLES) or not plan['groups']:
        raise ValueError('COMPLETE_CURRENT_SCHEMA_SNAPSHOT_REQUIRED')
    aliases = result['aliases']
    ids = set(aliases)
    if any(r['trip_id'] in ids for table in ('trip_invites','share_links') for r in tables[table]):
        raise ValueError('LINK_OR_INVITE_ACCESS_REVIEW_REQUIRED')
    payments = {p['id']: p for row in tables['trip_ledgers'] if row['trip_id'] in ids for p in row['state_json'].get('payments',[])}
    records = {r['id']:r for r in tables['payment_records'] if r['trip_id'] in ids}
    if set(payments) != set(records):
        raise ValueError('PAYMENT_RECONCILIATION_REQUIRED')
    for pid, payment in payments.items():
        if records[pid]['payload_json'] != payment:
            raise ValueError('PAYMENT_RECONCILIATION_REQUIRED')
        actual = {r['occurred_at']:Decimal(str(r['amount'])) for r in tables['payment_allocations'] if r['payment_id']==pid}
        expected = {r['date']:Decimal(str(r['amount'])) for r in payment['allocations']}
        if actual != expected:
            raise ValueError('ALLOCATION_RECONCILIATION_REQUIRED')
    statements = ['-- Offline generated migration. Review and execute separately as database owner in a maintenance window.',
                  'BEGIN;', "SET LOCAL lock_timeout='10s';", "SET LOCAL statement_timeout='120s';",
                  "SELECT pg_advisory_xact_lock(20261026, 1);",
                  'LOCK TABLE '+', '.join('public.'+t for t in SQL_TABLES)+' IN EXCLUSIVE MODE;']
    # Exact full-table comparisons reject stale exports, including concurrent revisions,
    # auth records, audit payloads, snapshots and receipt jobs. Locks prevent new writes.
    for table in SQL_TABLES:
        expected = sql_json(tables[table])
        statements.append(f"DO $$ BEGIN IF (SELECT coalesce(jsonb_agg(row ORDER BY row::text),'[]'::jsonb) FROM (SELECT to_jsonb(t) AS row FROM public.{table} t) q) IS DISTINCT FROM (SELECT coalesce(jsonb_agg(value ORDER BY value::text),'[]'::jsonb) FROM jsonb_array_elements({expected})) THEN RAISE EXCEPTION 'SOURCE_CHANGED_{table}'; END IF; END $$;")
    # Archive is deliberately inaccessible to client roles; it is the rollback/audit basis.
    statements += ["CREATE TABLE IF NOT EXISTS public.trip_merge_archives (digest text PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(), source_json jsonb NOT NULL, plan_json jsonb NOT NULL);",
                   'ALTER TABLE public.trip_merge_archives ENABLE ROW LEVEL SECURITY;',
                   'REVOKE ALL ON public.trip_merge_archives FROM PUBLIC,anon,authenticated;',
                   f"INSERT INTO public.trip_merge_archives(digest,source_json,plan_json) VALUES('{confirmation}',{sql_json(source)},{sql_json(plan)});"]
    for group in plan['groups']:
        target = group['target']
        target_expr = sql_json(target)+'#>>\'{}\''
        ids_expr = sql_json(group['source_ids'])
        predicate = f"trip_id IN (SELECT jsonb_array_elements_text({ids_expr}))"
        # Move payment projections first, so the normal immutable-payment trigger
        # can validate the merged ledger without detecting another trip's payment.
        for table in ('payment_records','expenses','receipt_images','settlement_versions'):
            statements.append(f'UPDATE public.{table} SET trip_id={target_expr} WHERE {predicate};')
        statements.append(f"DELETE FROM public.trip_members WHERE {predicate} AND trip_id<>{target_expr};")
        statements.append(f"DELETE FROM public.trip_ledgers WHERE {predicate} AND trip_id<>{target_expr};")
        ledger = next(r for r in result['tables']['trip_ledgers'] if r['trip_id']==target)
        revision = max([r['revision'] for r in tables['trip_ledgers'] if r['trip_id'] in group['source_ids']] or [0])+1
        statements.append(f"INSERT INTO public.trip_ledgers(trip_id,revision,state_json) VALUES({target_expr},{revision},{sql_json(ledger['state_json'])}) ON CONFLICT(trip_id) DO UPDATE SET revision=EXCLUDED.revision,state_json=EXCLUDED.state_json;")
        trip = next(r for r in result['tables']['trips'] if r['id']==target)
        statements.append(f"UPDATE public.trips SET name=({sql_json(trip['name'])}#>>'{{}}'),start_date='{trip['start_date']}'::date,end_date='{trip['end_date']}'::date,latest_version_id=NULL WHERE id={target_expr};")
        statements.append(f"DELETE FROM public.trips WHERE id IN (SELECT jsonb_array_elements_text({ids_expr})) AND id<>{target_expr};")
    # Compare every projected final table, allowing only the intentionally advanced ledger revision.
    for table in SQL_TABLES:
        expected_rows = deepcopy(result['tables'].get(table, []))
        if table == 'trip_ledgers':
            for row in expected_rows:
                if row['trip_id'] in {g['target'] for g in plan['groups']}:
                    group=next(g for g in plan['groups'] if g['target']==row['trip_id'])
                    row['revision']=max([r['revision'] for r in tables[table] if r['trip_id'] in group['source_ids']] or [0])+1
        statements.append(f"DO $$ BEGIN IF (SELECT coalesce(jsonb_agg(row ORDER BY row::text),'[]'::jsonb) FROM (SELECT to_jsonb(t) AS row FROM public.{table} t) q) IS DISTINCT FROM (SELECT coalesce(jsonb_agg(value ORDER BY value::text),'[]'::jsonb) FROM jsonb_array_elements({sql_json(expected_rows)})) THEN RAISE EXCEPTION 'FINAL_MISMATCH_{table}'; END IF; END $$;")
    statements += ['SET CONSTRAINTS ALL IMMEDIATE;', 'COMMIT;']
    return '\n'.join(statements)+'\n'

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["preview", "apply", "prepare-sql"])
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--confirm")
    args = parser.parse_args()
    try:
        source = load(args.source)
        if args.action == "preview":
            result = preview(source)
        else:
            if not args.plan or not args.confirm:
                raise ValueError("EXPLICIT_PLAN_AND_DIGEST_REQUIRED")
            plan = json.loads(args.plan.read_text(encoding="utf-8"))
            result = prepare_sql(source, plan, args.confirm) if args.action == 'prepare-sql' else apply(source, plan, args.confirm)
        if args.action == 'prepare-sql':
            with open(args.output, 'x', encoding='utf-8') as stream:
                stream.write(result)
        else:
            write_new(args.output, result)
    except (ValueError, KeyError, TypeError, OSError):
        parser.exit(1, "Offline operation rejected; source/output details suppressed. Review format, plan digest, and unused output path.\n")
    if args.action == "preview":
        print(f"Preview saved; candidate groups: {len(result['groups'])}; blocked: {len(result['blocked'])}; confirmation digest: {result['digest']}")
    else:
        print("New offline SQL saved; not executed." if args.action == 'prepare-sql' else "New offline review package saved. Source unchanged.")

if __name__ == "__main__":
    main()
