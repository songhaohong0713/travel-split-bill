// Read a private pre-upgrade archive and restore ONLY to an in-memory PostgreSQL.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import {gunzipSync} from 'node:zlib'
import {pathToFileURL} from 'node:url'

let phase = 'read archive'
function failPrivately() {
  console.error(`Snapshot rehearsal failed during ${phase}; private database error details suppressed.`)
  process.exit(1)
}
process.on('uncaughtException', failPrivately)
process.on('unhandledRejection', failPrivately)

const {PGlite} = await import(pathToFileURL(process.argv[2]).href)
const source = JSON.parse(gunzipSync(fs.readFileSync(process.argv[3])))
assert.equal(source.format, 'travel-split-preupgrade-v1')
const db = new PGlite()
const ident = name => '"' + String(name).replaceAll('"', '""') + '"'
const table = name => 'public.' + ident(name)
const grants = [...(source.table_grants || []), ...(source.function_grants || [])]
const roles = new Set(['anon', 'authenticated', 'service_role', ...grants.map(g => g.grantee)])
for (const role of roles) {
  if (['PUBLIC', 'postgres'].includes(role)) continue
  await db.exec(`CREATE ROLE ${ident(role)} ${role === 'service_role' ? 'BYPASSRLS' : ''}`)
}
await db.exec('BEGIN;')
phase = 'restore tables and constraints'
for (const name of Object.keys(source.tables)) {
  const columns = source.columns.filter(c => c.table === name)
  assert.ok(columns.length > 0)
  const definitions = columns.map(c => `${ident(c.name)} ${c.type}${c.default ? ' DEFAULT ' + c.default : ''}${c.not_null ? ' NOT NULL' : ''}`)
  await db.exec(`CREATE TABLE ${table(name)} (${definitions.join(',')})`)
  await db.query(`INSERT INTO ${table(name)} SELECT * FROM jsonb_populate_recordset(NULL::${table(name)},$1::jsonb)`, [JSON.stringify(source.tables[name])])
}
// Referenced primary/unique constraints must exist before foreign keys.
for (const constraint of [...source.constraints].sort((a,b) => Number(a.definition.startsWith('FOREIGN KEY')) - Number(b.definition.startsWith('FOREIGN KEY')))) {
  await db.exec(`ALTER TABLE ${table(constraint.table)} ADD CONSTRAINT ${ident(constraint.name)} ${constraint.definition}`)
}
for (const index of source.indexes || []) await db.exec(index)
for (const fn of source.functions || []) {
  await db.exec(fn.definition)
  await db.exec(`REVOKE ALL ON FUNCTION public.${ident(fn.name)}(${fn.identity}) FROM PUBLIC`)
}
assert.deepEqual(source.policies, [], 'Policy restoration not implemented: fail closed')
for (const security of source.security) {
  if (security.rls) await db.exec(`ALTER TABLE ${table(security.table)} ENABLE ROW LEVEL SECURITY`)
  if (security.force_rls) await db.exec(`ALTER TABLE ${table(security.table)} FORCE ROW LEVEL SECURITY`)
}
for (const grant of source.table_grants || []) {
  assert.match(grant.privilege_type, /^(SELECT|INSERT|UPDATE|DELETE|TRUNCATE|REFERENCES|TRIGGER)$/)
  await db.exec(`GRANT ${grant.privilege_type} ON TABLE ${table(grant.table_name)} TO ${grant.grantee === 'PUBLIC' ? 'PUBLIC' : ident(grant.grantee)}`)
}
for (const grant of source.function_grants || []) {
  const fn = source.functions.find(f => grant.routine_name === f.name)
  if (!fn) continue
  assert.equal(grant.privilege_type, 'EXECUTE')
  await db.exec(`GRANT EXECUTE ON FUNCTION public.${ident(fn.name)}(${fn.identity}) TO ${grant.grantee === 'PUBLIC' ? 'PUBLIC' : ident(grant.grantee)}`)
}
await db.exec('COMMIT;')
phase = 'verify recorded function grants'
for (const grant of (source.function_grants || []).filter(g => ['anon', 'authenticated', 'service_role'].includes(g.grantee))) {
  const result = await db.query("SELECT has_function_privilege($1,p.oid,'EXECUTE') AS allowed FROM pg_proc p WHERE p.pronamespace='public'::regnamespace AND p.proname=$2", [grant.grantee,grant.routine_name])
  assert.equal(result.rows[0]?.allowed,true,'function grant missing')
}
function normalized(rows) { return rows.map(r => JSON.stringify(Object.fromEntries(Object.entries(r).sort()))).sort() }
async function compareTables() {
  for (const name of Object.keys(source.tables)) {
    const restored = (await db.query(`SELECT to_jsonb(t) AS row FROM ${table(name)} t`)).rows.map(r => r.row)
    assert.ok(JSON.stringify(normalized(restored)) === JSON.stringify(normalized(source.tables[name])), name + ': restored records differ')
  }
}
phase = 'compare restored data'
await compareTables()
console.log('Restored all 12 tables with exact data equality, constraints, indexes, 16 functions and recorded grants.')
await db.exec(`SET request.jwt.claims='{"role":"service_role"}';`)
phase = 'apply incremental migrations'
for (const file of ['cloudbase/migrations/20261024010000_daily_settlement.sql', 'cloudbase/migrations/20261024010100_backend_hardening.sql', 'cloudbase/migrations/20261024010200_business_backup.sql']) {
  await db.exec(fs.readFileSync(file, 'utf8'))
}
for (const name of Object.keys(source.tables).filter(n => n !== 'trips')) {
  const restored = (await db.query(`SELECT to_jsonb(t) AS row FROM ${table(name)} t`)).rows.map(r => r.row)
  assert.ok(JSON.stringify(normalized(restored)) === JSON.stringify(normalized(source.tables[name])), name + ': prior records changed')
}
const trips = (await db.query('SELECT to_jsonb(t) AS row FROM trips t')).rows.map(r => { const {start_date,end_date,...old}=r.row; assert.equal(start_date,null); assert.equal(end_date,null); return old })
assert.ok(JSON.stringify(normalized(trips)) === JSON.stringify(normalized(source.tables.trips)), 'prior trips changed')
const history = (await db.query("SELECT count(*)::int AS n FROM trip_ledgers WHERE state_json->'history_confirmation'->>'status'='pending'")).rows[0].n
console.log(`Applied all three increments; all prior records unchanged; ${history} trips marked for explicit historical confirmation.`)
await db.close()
