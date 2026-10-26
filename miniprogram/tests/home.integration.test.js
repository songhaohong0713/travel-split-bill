const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path')
function page(name,api,wx={}){
 let definition
 const uiModule={exports:{}}
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../services/ledger-view.js'),'utf8'),{module:uiModule,wx:{showModal(o){o.success({confirm:true})},...wx},Date,Promise,getApp(){return {globalData:{}}}})
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,`../pages/${name}/index.js`),'utf8'),{Page(d){definition=d},require(n){if(n.includes('ledger-view'))return uiModule.exports;if(n.includes('member-labels'))return require('../services/member-labels');return api},wx:{showToast(){},...wx},Date,Promise})
 return {...definition,data:JSON.parse(JSON.stringify(definition.data)),setData(c){Object.assign(this.data,c)}}
}
test('home authenticated list opens detail, not expense; refresh removes deleted trips',async()=>{
 let trips=[{id:'t',name:'东京',default_currency:'CNY',member_count:2}],nav=[]
 const p=page('trips',{login:async()=>{},listTrips:async()=>trips,ledgerSummary:async()=>({trips:[],groups:[]})},{navigateTo(o){nav.push(o.url)}})
 await p.onLoad();p.openTrip({currentTarget:{dataset:{id:'t'}}});assert.match(nav[0],/pages\/trip-detail/);assert.equal(p.data.authFailed,false)
 trips=[];await p.onShow();assert.equal(p.data.trips.length,0)
})
test('only login failure shows login notice, successful retry clears it',async()=>{
 let fail=true
 const p=page('trips',{login:async()=>{if(fail)throw Error()},listTrips:async()=>[],ledgerSummary:async()=>({trips:[],groups:[]})})
 await p.onLoad();assert.equal(p.data.authFailed,true);fail=false;await p.retryLogin();assert.equal(p.data.authFailed,false)
})
test('create prompts for duplicate and never creates after cancel',async()=>{
 let calls=0
 const p=page('trip-create',{ensureAuthenticated:async()=>{},listTrips:async()=>[{name:'东京'}],createTrip:async()=>{calls++}},{showModal(o){o.success({confirm:false})}})
 p.setData({name:'东京'});await p.createTrip();assert.equal(calls,0);assert.equal(p.data.creating,false)
})
test('creation redirects to detail invitation guide even when optional dates fail',async()=>{
 let url='';let calls=0
 const p=page('trip-create',{ensureAuthenticated:async()=>{},listTrips:async()=>[],createTrip:async()=>{calls++;return{id:'t',default_currency:'CNY'}},updateTripDates:async()=>{throw Error()}},{redirectTo(o){url=o.url}})
 p.setData({name:'东京',startDate:'2026-10-26'});await p.createTrip();assert.equal(calls,1);assert.match(url,/trip-detail/);assert.match(url,/inviteGuide=1/);assert.match(url,/dateFailed=1/)
})
module.exports={page}
