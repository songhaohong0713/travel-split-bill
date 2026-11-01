// Synthetic in-memory PostgreSQL only. Arguments: PGlite module path, Python executable.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import {spawnSync} from 'node:child_process'
import {pathToFileURL} from 'node:url'
const {PGlite}=await import(pathToFileURL(process.argv[2]).href)
const python=process.argv[3]
const db=new PGlite()
const folder=fs.mkdtempSync(path.join(os.tmpdir(),'trip-merge-synthetic-'))
const command=(...args)=>{
  const result=spawnSync(python,['scripts/trip-merge.py',...args],{encoding:'utf8'})
  assert.equal(result.status,0,result.stderr)
  return result
}
try {
  await db.exec('CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role BYPASSRLS;')
  for(const file of ['infra/cloudbase/initial_postgresql_schema.sql','infra/cloudbase/20260919_trip_collaboration.sql','cloudbase/migrations/20261024010000_daily_settlement.sql','cloudbase/migrations/20261024010100_backend_hardening.sql','cloudbase/migrations/20261024010200_business_backup.sql']) await db.exec(fs.readFileSync(file,'utf8'))
  await db.exec(`SET request.jwt.claims='{"role":"service_role"}';
    INSERT INTO users(id) VALUES('a'),('b');
    INSERT INTO trips(id,owner_id,name,default_currency) VALUES('t1','a','1001札幌','JPY'),('t2','a','1002札幌','JPY');
    INSERT INTO trip_members(id,trip_id,user_id) VALUES('m1','t1','a'),('m2','t1','b'),('m3','t2','a'),('m4','t2','b');
    INSERT INTO expenses(id,trip_id,owner_id,revision,occurred_at,payload_json) VALUES('e1','t1','a',1,'2026-10-01','{"original_splits":{"a":"10","b":"20"}}'),('e2','t2','b',2,'2026-10-02','{"original_splits":{"a":"30","b":"40"}}');
    INSERT INTO settlement_versions(id,trip_id,result_json,public_json) VALUES('v1','t2','{"calculation":"only"}','{}');
    INSERT INTO receipt_images(id,owner_id,trip_id,object_key,status) VALUES('image','a','t2','original/t2/image.jpg','ready');
    INSERT INTO receipt_jobs(id,image_id,status,attempts) VALUES('job','image','done',1);`)
  const payment={id:'p',currency:'JPY',amount:'30',from_participant_id:'b',to_participant_id:'a',created_by:'a',created_at:'2026-10-02T10:00:00Z',revoked_at:'2026-10-03T10:00:00Z',revoked_by:'b',allocations:[{date:'2026-10-02',amount:'30'}]}
  await db.query('INSERT INTO trip_ledgers(trip_id,revision,state_json) VALUES($1,$2,$3)',['t1',3,{closed_dates:['2026-10-01'],payments:[]}])
  await db.query('INSERT INTO trip_ledgers(trip_id,revision,state_json) VALUES($1,$2,$3)',['t2',8,{closed_dates:['2026-10-02'],payments:[payment]}])
  const source=(await db.query('SELECT tsb_export_business_backup() AS v')).rows[0].v
  const src=path.join(folder,'source.json'),plan=path.join(folder,'plan.json'),sqlFile=path.join(folder,'merge.sql')
  fs.writeFileSync(src,JSON.stringify(source))
  command('preview','--source',src,'--output',plan)
  const digest=JSON.parse(fs.readFileSync(plan,'utf8')).digest
  command('prepare-sql','--source',src,'--plan',plan,'--confirm',digest,'--output',sqlFile)
  const sql=fs.readFileSync(sqlFile,'utf8')
  // Fail-closed guard exercises stale revision before any archive or remapping.
  await db.exec("UPDATE trip_ledgers SET revision=revision+1 WHERE trip_id='t2'")
  await assert.rejects(()=>db.exec(sql),/SOURCE_CHANGED_trip_ledgers/)
  await db.exec("ROLLBACK; UPDATE trip_ledgers SET revision=revision-1 WHERE trip_id='t2';")
  await db.exec(sql)
  assert.equal((await db.query('SELECT count(*)::int AS n FROM trips')).rows[0].n,1)
  assert.equal((await db.query("SELECT revision FROM trip_ledgers WHERE trip_id='t1'")).rows[0].revision,9)
  assert.equal((await db.query("SELECT trip_id FROM receipt_images WHERE id='image'")).rows[0].trip_id,'t1')
  assert.equal((await db.query("SELECT trip_id FROM payment_records WHERE id='p'")).rows[0].trip_id,'t1')
  assert.deepEqual((await db.query("SELECT payload_json FROM payment_records WHERE id='p'")).rows[0].payload_json,payment)
  assert.equal((await db.query('SELECT count(*)::int AS n FROM payment_allocations')).rows[0].n,1)
  const archived=(await db.query('SELECT source_json FROM trip_merge_archives')).rows[0].source_json
  assert.deepEqual(archived,source)
  assert.equal((await db.query("SELECT tsb_ledger_snapshot('t1','b') AS v")).rows[0].v.members.length,2)
  await assert.rejects(()=>db.exec(sql),/SOURCE_CHANGED/)
  await db.exec('ROLLBACK; SET ROLE authenticated;')
  await assert.rejects(()=>db.query('SELECT * FROM trip_merge_archives'))
  console.log('Passed synthetic SQL merge: stale/replay guards, immutable payments/revocation, allocation/receipt/snapshot preservation, revision, member access and private archive permissions.')
} finally {
  await db.close()
  fs.rmSync(folder,{recursive:true,force:true})
}
