// Run with a disposable PGlite installation, never against a remote database:
// node scripts/verify-ledger-pg.mjs <absolute-path-to-@electric-sql/pglite/dist/index.js>
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { pathToFileURL } from 'node:url'

const { PGlite } = await import(pathToFileURL(process.argv[2]).href)
const db = new PGlite()
await db.exec('CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role BYPASSRLS;')
for (const file of ['infra/cloudbase/initial_postgresql_schema.sql','infra/cloudbase/20260919_trip_collaboration.sql','cloudbase/migrations/20261024010000_daily_settlement.sql']) {
  await db.exec(fs.readFileSync(file,'utf8'))
}
await db.exec(`SET request.jwt.claims = '{"role":"service_role"}';
INSERT INTO users(id) VALUES('a'),('b');
INSERT INTO trips(id,owner_id,name,default_currency) VALUES('t','a','Tokyo','CNY');
INSERT INTO trip_members(id,trip_id,user_id) VALUES('m1','t','a'),('m2','t','b');`)
const snapshot = async user => (await db.query('SELECT tsb_ledger_snapshot($1,$2) AS result',['t',user])).rows[0].result
assert.equal((await snapshot('outsider')).not_found,true)
const before = await snapshot('a')
const state = {closed_dates:['2026-10-07'],payments:[]}
const commit = async expected => (await db.query('SELECT tsb_commit_ledger($1,$2,$3,$4) AS result',['t','b',expected,state])).rows[0].result
assert.equal((await commit(before)).ok,true)
assert.equal((await commit(before)).conflict,true)
const current = await snapshot('a')
await db.exec(`INSERT INTO expenses(id,trip_id,owner_id,revision,occurred_at,payload_json) VALUES('e','t','a',1,'2026-10-07','{}');`)
assert.equal((await commit(current)).conflict,true)
await db.query('SELECT tsb_update_trip_dates($1,$2,$3,$4)',['t','a','2026-10-05','2026-10-08'])
assert.equal((await db.query('SELECT start_date::text FROM trips')).rows[0].start_date,'2026-10-05')
await db.exec(`SET ROLE authenticated; SET request.jwt.claims = '{"role":"authenticated"}';`)
await assert.rejects(() => snapshot('a'))
await assert.rejects(() => db.query('SELECT * FROM trip_ledgers'))
await db.close()
console.log('PostgreSQL migration, snapshot, CAS, expense invalidation, dates and access control passed.')
