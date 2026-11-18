const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path')
function page(api,wx={}){let definition;const m={exports:{}};vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../services/ledger-view.js'),'utf8'),{module:m,wx,Date,Promise,require(n){return require('../services/'+n)}});vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../pages/trip-detail/index.js'),'utf8'),{Page(d){definition=d},require(n){return n.includes('ledger-view')?m.exports:n.includes('member-labels')?require('../services/member-labels'):n.includes('money')?require('../services/money'):n.includes('offline-trips')?{cacheLedger(){},isLocalTrip(){return false}}:api},wx:{showToast(){},...wx},Date,Promise});return {...definition,data:JSON.parse(JSON.stringify(definition.data)),setData(c){Object.assign(this.data,c)}}}
function api(){return {listTrips:async()=>[{id:'t',name:'东京',default_currency:'CNY',member_count:1,is_owner:true}],listTripMembers:async()=>[{id:'a',is_current:true}],listExpenses:async()=>[],getLedger:async()=>({members:['a'],days:[],summary:[],payments:[]}),createTripInvite:async()=>({token:'secure-token'})}}
test('new trip automatically opens skippable invite; native share preserves server token',async()=>{const p=page(api());await p.onLoad({tripId:'t',inviteGuide:'1'});assert.equal(p.data.inviteOpen,true);assert.match(p.onShareAppMessage().path,/trip-invite\/index\?token=secure-token/);p.closeInvite();assert.equal(p.data.inviteOpen,false)})
test('trip refresh uses day cards; two or more pending dates enable merge count',async()=>{const a=api();a.getLedger=async()=>({members:['a','b'],summary:[{currency:'CNY',amount:'30',to_participant_id:'a'}],payments:[],days:['2026-10-24','2026-10-25'].map(date=>({date,status:'pending',count:1,pending:[]}))});const p=page(a);await p.onLoad({tripId:'t'});assert.equal(p.data.days.length,2);assert.equal(p.data.pendingDays,2);assert.equal(p.data.summary[0].direction,'同行人待付你')})
test('invited member cannot generate invite',async()=>{const a=api();a.listTrips=async()=>[{id:'t',name:'东京',is_owner:false,member_count:2}];let calls=0;a.createTripInvite=async()=>{calls++};const p=page(a);await p.onLoad({tripId:'t',inviteGuide:'1'});await p.prepareInvite();assert.equal(calls,0)})

test('skipping invite stays skipped on the next visit',async()=>{
 const saved={};const wx={getStorageSync:k=>saved[k],setStorageSync:(k,v)=>saved[k]=v};
 const first=page(api(),wx);await first.onLoad({tripId:'t',inviteGuide:'1'});first.closeInvite();
 const second=page(api(),wx);await second.onLoad({tripId:'t',inviteGuide:'1'});assert.equal(second.data.inviteOpen,false);
});

test('settings save ignores double taps and blocks deletion while saving',async()=>{
 const a=api();let release,writes=0; a.updateTripNickname=()=>{writes++;return new Promise(resolve=>release=resolve)};a.updateTripDates=async()=>{};
 const p=page(a);p.setData({nickname:'小周',isOwner:true,draftName:'东京',name:'东京',tripId:'t'});
 const first=p.saveManage();await p.saveManage();await p.deleteManagedTrip();assert.equal(p.data.managing,true);assert.equal(writes,1);release();await first;assert.equal(p.data.managing,false);
});

test('settings save gives success feedback only after requests complete',async()=>{
 const a=api(),messages=[];a.updateTripNickname=async()=>{};a.updateTripDates=async()=>{};const p=page(a,{showToast:o=>messages.push(o.title)});p.setData({nickname:'驴',isOwner:true,draftName:'东京',name:'东京',tripId:'t'});await p.saveManage();assert.ok(messages.includes('已保存设置'));assert.equal(p.data.managing,false);
});

test('trip hero distinguishes recording, empty and unavailable summaries',async()=>{
 const a=api(),p=page(a);await p.onLoad({tripId:'t'});assert.equal(p.data.heroState,'empty');
 a.listExpenses=async()=>[{id:'r',occurred_at:'2026-10-28',payload:{settlement_currency:'CNY',expenses:[{payer_id:'a',items:[{amount:{currency:'CNY',amount:'10'}}]}]}}];
 a.getLedger=async()=>({days:[{date:'2026-10-28',status:'recording',count:1,pending:[]}],payments:[],summary:[],members:['a']});await p.reload();assert.equal(p.data.heroState,'recording');
 a.getLedger=async()=>({days:[],payments:[],summary:[],compatibilityError:'服务版本不匹配'});await p.reload();assert.equal(p.data.heroState,'unavailable');assert.equal(p.data.hasSummary,false);
});

 test('only an owner can set a legacy trip timezone and cancellation does not write',async()=>{
 const a=api();let writes=0;a.setTripTimezone=async()=>writes++;let allow=false;
 const p=page(a,{showModal:o=>o.success({confirm:allow})});p.reload=async()=>{};p.setData({tripId:'t',isOwner:false,timezoneNeedsSetup:true});
 await p.setTimezone({detail:{value:0}});assert.equal(writes,0);
 p.data.isOwner=true;await p.setTimezone({detail:{value:0}});assert.equal(writes,0);
 allow=true;await p.setTimezone({detail:{value:0}});assert.equal(writes,1);
 p.data.timezoneNeedsSetup=false;await p.setTimezone({detail:{value:0}});assert.equal(writes,1);
 });
