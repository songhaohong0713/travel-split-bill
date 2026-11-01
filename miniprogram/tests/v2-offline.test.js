const fs=require('node:fs'),vm=require('node:vm'),test=require('node:test'),assert=require('node:assert/strict')
function store(){const values={},module={exports:{}};vm.runInNewContext(fs.readFileSync(__dirname+'/../services/offline-trips.js','utf8'),{module,wx:{getStorageSync:k=>values[k],setStorageSync:(k,v)=>{values[k]=v}},Promise,Date,Math});return module.exports}
test('offline retry retains UUID and old service cannot receive mutating requests',async()=>{
 const o=store();o.setAccount('a');const t=o.enqueueTrip('东京','CNY');let writes=0
 await o.syncPending({request:async(path,opts)=>{if(opts)writes++;throw {statusCode:404}}})
 assert.equal(writes,0);assert.equal(o.pending()[0].id,t.id)
 const ids=[];await o.syncPending({request:async(path,opts)=>{if(!opts)return{client_trip_id:true};ids.push(opts.data.client_id)}})
 assert.deepEqual(ids,[t.id]);assert.equal(o.pending().length,0)
})
test('bound pending trips cannot upload or show for another account',async()=>{
 const o=store();o.setAccount('a');o.enqueueTrip('私有旅行','CNY');o.setAccount('b')
 assert.equal(o.listCachedTrips().length,0);let calls=0
 await o.syncPending({request:async()=>{calls++}});assert.equal(calls,0)
})

test('home cache preserves server summaries across plain trip-list refresh and isolates accounts',()=>{
 const o=store();o.setAccount('a');o.rememberTrips([{id:'t',name:'东京',_summary:{day_count:2},companion_name:'小周'}]);o.cacheHomeSummary({summaryGroups:[{currency:'CNY',receivableText:'¥20.00'}]});
 o.rememberTrips([{id:'t',name:'新名称'}]);assert.equal(o.listCachedTrips()[0]._summary.day_count,2);assert.equal(o.listCachedTrips()[0].name,'新名称');
 o.setAccount('b');assert.equal(o.readHomeSummary(),null);assert.equal(o.listCachedTrips().length,0);
});

test('known successful trip deletion removes only this account cached trip and summary',()=>{
 const o=store();o.setAccount('a');o.rememberTrips([{id:'gone'},{id:'keep'}]);o.cacheHomeSummary({summarySource:'gone'});o.setAccount('b');o.rememberTrips([{id:'gone'}]);o.setAccount('a');o.forgetTrip('gone');assert.deepEqual(Array.from(o.listCachedTrips(),t=>t.id),['keep']);assert.equal(o.readHomeSummary(),null);o.setAccount('b');assert.equal(o.listCachedTrips().length,1);
});
