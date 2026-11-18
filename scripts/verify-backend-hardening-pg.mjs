// Disposable PostgreSQL WASM verification, never connects to production.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import {spawnSync} from 'node:child_process'
import {pathToFileURL} from 'node:url'

process.on('uncaughtException',error=>{console.error(error.message,error.detail || '',error.where || '');process.exit(1)})
const {PGlite}=await import(pathToFileURL(process.argv[2]).href)
const python=process.argv[3]
const migrations=['infra/cloudbase/initial_postgresql_schema.sql','infra/cloudbase/20260919_trip_collaboration.sql','cloudbase/migrations/20261024010000_daily_settlement.sql']
async function initial(db) {
  await db.exec('CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role BYPASSRLS;')
  for(const file of migrations) await db.exec(fs.readFileSync(file,'utf8'))
  await db.exec(`SET request.jwt.claims='{"role":"service_role"}';`)
}
async function harden(db) {
  for(const file of ['cloudbase/migrations/20261024010100_backend_hardening.sql','cloudbase/migrations/20261024010200_business_backup.sql']) await db.exec(fs.readFileSync(file,'utf8'))
}
const db=new PGlite()
await initial(db)
await db.exec(`INSERT INTO users(id) VALUES('a'),('b');
INSERT INTO trips(id,owner_id,name,default_currency) VALUES('old','a','Old trip','CNY');
INSERT INTO trip_members(id,trip_id,user_id) VALUES('m1','old','a'),('m2','old','b');
INSERT INTO expenses(id,trip_id,owner_id,revision,occurred_at,payload_json) VALUES('e','old','a',1,'2026-10-07','{}');`)
await harden(db)
let snapshot=(await db.query("SELECT tsb_ledger_snapshot('old','a') AS v")).rows[0].v
assert.equal(snapshot.state.history_confirmation.status,'pending')
const state={...snapshot.state,closed_dates:['2026-10-07'],payments:[{id:'p',currency:'CNY',amount:'200.00',from_participant_id:'b',to_participant_id:'a',created_by:'a',created_at:'2026-10-22T00:00:00Z',allocations:[{date:'2026-10-07',amount:'200.00'}]}]}
const commit=()=>db.query("SELECT tsb_commit_ledger('old','a',$1,$2) AS v",[snapshot,state])
const race=await Promise.all([commit(),commit()])
assert.equal(race.filter(r=>r.rows[0].v.ok).length,1)
assert.equal(race.filter(r=>r.rows[0].v.conflict).length,1)
assert.equal((await db.query('SELECT amount::text FROM payment_records')).rows[0].amount,'200.00')
assert.equal((await db.query('SELECT count(*)::int AS n FROM payment_allocations')).rows[0].n,1)
snapshot=(await db.query("SELECT tsb_ledger_snapshot('old','a') AS v")).rows[0].v
const invalid=structuredClone(snapshot.state)
invalid.payments[0].amount='201'
await assert.rejects(()=>db.query("SELECT tsb_commit_ledger('old','a',$1,$2)",[snapshot,invalid]))
assert.equal((await db.query('SELECT amount::text FROM payment_records')).rows[0].amount,'200.00')
const summary=(await db.query("SELECT tsb_member_ledger_snapshots('a') AS v")).rows
assert.equal(summary.length,1)
const backup=(await db.query('SELECT tsb_export_business_backup() AS v')).rows[0].v
const prepare=spawnSync(python,['-c',`import sys,json;from app.ops.business_backup import pack_backup,restore_sql;print(restore_sql(pack_backup(json.load(sys.stdin))))`],{input:JSON.stringify(backup),env:{...process.env,PYTHONPATH:'backend'},encoding:'utf8'})
assert.equal(prepare.status,0,prepare.stderr)
const restored=new PGlite()
await initial(restored)
await harden(restored)
await restored.exec(prepare.stdout)
const restoredBackup=(await restored.query('SELECT tsb_export_business_backup() AS v')).rows[0].v
for(const table of Object.keys(backup.tables)) assert.deepEqual(restoredBackup.tables[table],backup.tables[table],table)
await assert.rejects(()=>restored.exec(prepare.stdout))
await restored.exec('ROLLBACK;')
await db.exec(`SET ROLE authenticated; SET request.jwt.claims='{"role":"authenticated"}';`)
await assert.rejects(()=>db.query('SELECT tsb_export_business_backup()'))
await assert.rejects(()=>db.query('SELECT * FROM payment_records'))
await db.close();await restored.close()
console.log('Passed: migration, history pending, competing CAS, atomic projections, immutable amounts, batch summary, backup/restore equality, nonempty-target rejection and permissions.')
