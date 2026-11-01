// Disposable PostgreSQL semantics only. No network, no real account or ledger.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import {pathToFileURL} from 'node:url'
const {PGlite}=await import(pathToFileURL(process.argv[2]).href)
const db=new PGlite()
await db.exec('CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role BYPASSRLS;')
for(const file of ['initial_postgresql_schema.sql','20260919_trip_collaboration.sql','cloudbase_http_api_migration.sql','20260927_receipt_job_membership.sql'])await db.exec(fs.readFileSync('infra/cloudbase/'+file,'utf8'))
await db.exec(`CREATE SCHEMA storage; CREATE TABLE storage.buckets(id text PRIMARY KEY,name text,public boolean,file_size_limit bigint,allowed_mime_types text[]); CREATE TABLE storage.objects(bucket_id text,name text); ALTER TABLE storage.buckets ENABLE ROW LEVEL SECURITY; ALTER TABLE storage.objects ENABLE ROW LEVEL SECURITY; GRANT USAGE ON SCHEMA storage TO anon,authenticated,service_role; GRANT ALL ON storage.buckets,storage.objects TO anon,authenticated,service_role;`)
await db.exec(fs.readFileSync('cloudbase/migrations/20261031010000_receipt_originals.sql','utf8'))
const compatibility='cloudbase/migrations/20261031010100_receipt_job_stored.sql'
if(fs.existsSync(compatibility))await db.exec(fs.readFileSync(compatibility,'utf8'))
const png='cloudbase/migrations/20261101010000_receipt_png.sql'
if(fs.existsSync(png)){await db.exec(fs.readFileSync(png,'utf8'));assert.deepEqual((await db.query("SELECT allowed_mime_types FROM storage.buckets WHERE id='receipts'")).rows[0].allowed_mime_types,['image/jpeg','image/png'])}
await db.exec(`SET request.jwt.claims='{"role":"service_role"}';`)
await db.query("SELECT tsb_create_trip('trip-a','a','Tokyo','CNY')")
await db.query("SELECT tsb_create_trip('trip-b','a','Other','CNY')")
await db.exec("INSERT INTO trip_members(id,trip_id,user_id,joined_at) VALUES ('m-a','trip-a','a',now()),('m-b','trip-b','a',now()) ON CONFLICT DO NOTHING")
await db.query("SELECT tsb_create_receipt_image('image-a','a','trip-a','a/trip-a/image.jpg')")
await db.exec("UPDATE receipt_images SET status='stored' WHERE id='image-a'")
const result=(await db.query("SELECT tsb_create_receipt_job('job-a','image-a','a') AS value")).rows[0].value
assert.equal(result.id,'job-a','stored image must be accepted by the real receipt-job RPC')
const save=(id,trip,job)=>db.query(`INSERT INTO expenses(id,trip_id,owner_id,revision,occurred_at,payload_json,created_at) VALUES ($1,$2,'a',1,'2026-10-01',$3,now())`,[id,trip,JSON.stringify({receipt_job_id:job})])
await save('expense-a','trip-a','job-a')
await save('expense-b','trip-a','job-a')
await assert.rejects(()=>save('foreign','trip-b','job-a'),/TSB_RECEIPT_REFERENCE_INVALID/)
await db.exec("DELETE FROM expenses WHERE id='expense-a'")
assert.equal((await db.query('SELECT count(*)::int AS n FROM receipt_cleanup')).rows[0].n,0)
await db.exec("DELETE FROM expenses WHERE id='expense-b'")
assert.equal((await db.query('SELECT count(*)::int AS n FROM receipt_cleanup')).rows[0].n,1)
assert.equal((await db.query('SELECT count(*)::int AS n FROM receipt_images')).rows[0].n,0)
await db.exec("CREATE POLICY broad_read ON storage.objects FOR SELECT USING(true); INSERT INTO storage.objects VALUES ('receipts','private.jpg'),('other','public.jpg'); SET ROLE authenticated;")
assert.deepEqual((await db.query('SELECT bucket_id FROM storage.objects')).rows,[{bucket_id:'other'}])
await assert.rejects(()=>db.query('SELECT * FROM public.receipt_cleanup'),/permission denied/)
await db.close()
console.log('PG receipt RPC, cross-trip guard, shared reference cleanup, durable outbox and private RLS passed.')
