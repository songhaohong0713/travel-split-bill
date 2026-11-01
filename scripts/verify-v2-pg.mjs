// Disposable PostgreSQL only; no remote connection.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import {pathToFileURL} from 'node:url'
const {PGlite}=await import(pathToFileURL(process.argv[2]).href)
const db=new PGlite()
await db.exec('CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role BYPASSRLS;')
for(const file of ['infra/cloudbase/initial_postgresql_schema.sql','infra/cloudbase/20260919_trip_collaboration.sql','cloudbase/migrations/20261024010000_daily_settlement.sql','cloudbase/migrations/20261024010100_backend_hardening.sql','cloudbase/migrations/20261027030000_offline_trip.sql']) await db.exec(fs.readFileSync(file,'utf8'))
await db.exec(fs.readFileSync('cloudbase/migrations/20261027030000_offline_trip.sql','utf8'))
await db.exec(`SET request.jwt.claims='{"role":"service_role"}';`)
const create=(owner='a',name='Tokyo')=>db.query("SELECT tsb_create_trip('uuid', $1, $2, 'CNY') AS v",[owner,name])
assert.equal((await create()).rows[0].v.id,'uuid')
assert.equal((await create()).rows[0].v.id,'uuid')
assert.equal((await create('b')).rows[0].v.conflict,true)
assert.equal((await create('a','Different')).rows[0].v.conflict,true)
assert.equal((await db.query('SELECT count(*)::int AS n FROM trips')).rows[0].n,1)
assert.equal((await db.query('SELECT count(*)::int AS n FROM trip_members')).rows[0].n,1)
const before=(await db.query("SELECT tsb_ledger_snapshot('uuid','a') AS v")).rows[0].v
const state={...before.state,payer_marks:[{id:'mark',currency:'CNY',amount:'1.00',marked_by:'a'}]}
assert.equal((await db.query("SELECT tsb_commit_ledger('uuid','a',$1,$2) AS v",[before,state])).rows[0].v.ok,true)
assert.equal((await db.query('SELECT count(*)::int AS n FROM payment_records')).rows[0].n,0)
assert.equal((await db.query("SELECT tsb_commit_ledger('uuid','a',$1,$2) AS v",[before,state])).rows[0].v.conflict,true)
await db.exec(`SET ROLE authenticated; SET request.jwt.claims='{"role":"authenticated"}';`)
await assert.rejects(()=>create())
await db.close()
console.log('v2 UUID replay, conflicts, migration replay, marks without payments, CAS and privileges passed.')
