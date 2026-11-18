const test=require('node:test');
const assert=require('node:assert/strict');
const ui=require('../services/ledger-view');
test('v2 cards use explicit dates and invite/settled states',()=>{
 const invite=ui.tripCard({id:'t',name:'东京',member_count:1},{latest_expense_date:ui.today()});
 assert.equal(invite.ongoing,false);assert.equal(invite.state,'invite');assert.equal(invite.avatar,'东');
 assert.equal(ui.tripCard({name:'京都',member_count:2},{}).state,'empty');
});
test('v2 sorting preserves invite trips when settled trips hidden',()=>{
 const cards=[ui.tripCard({id:'a',name:'a',member_count:2},{day_count:1}),ui.tripCard({id:'b',name:'b',member_count:1},{})];
 assert.deepEqual(ui.sortTrips(cards,'date',true).map(x=>x.id),['b']);
});
test('v2 summaries never net receivable against payable',()=>{
 const [g]=ui.summaryGroups([{currency:'CNY',receivable:'320.00',payable:'80.00'}]);
 assert.equal(g.receivableText,'¥320.00');assert.equal(g.payableText,'¥80.00');
});
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function loadPage(name,api,wxOverrides={}){
 let page;const calls=[];
 const offline={listCachedTrips:()=>[],rememberTrips(){},cacheLedger(){}};
 const wx={getStorageSync(){},setStorageSync(){},showToast(){},showModal(o){o.success({confirm:true})},setClipboardData(o){calls.push(o.data)},...wxOverrides};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../pages',name,'index.js'),'utf8'),{Page(p){page=p},require(n){if(n.endsWith('/ledger-view')){const module={exports:{}};vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../services/ledger-view.js'),'utf8'),{module,wx,require(r){return r==='./api'?api:require(path.join(__dirname,'../services',r))},Date,Promise});return module.exports}if(n.endsWith('/api'))return api;if(n.endsWith('/offline-trips'))return offline;return require(path.join(__dirname,'../pages',name,n))},wx,Date,Promise});
 return {...page,data:structuredClone(page.data),setData(v){Object.assign(this.data,v)},calls};
}

test('bill opened by trip ID resolves its name and retains heading during refresh',async()=>{
 let finish;
 const api={listTrips:async()=>[{id:'t',name:'札幌旅行'}],getLedger:async()=>({days:[{date:'2026-10-29',status:'pending',pending:[],groups:[]}],payments:[]}),listExpenses:async()=>[],listTripMembers:async()=>[{id:'me',is_current:true},{id:'friend'}]};
 const p=loadPage('day-bill',api);await p.onLoad({tripId:'t',date:'2026-10-29'});assert.equal(p.data.name,'札幌旅行');assert.equal(p.data.loadedView,true);
 api.getLedger=()=>new Promise(r=>finish=r);const reload=p.reload();assert.equal(p.data.loading,true);assert.equal(p.data.loadedView,true);assert.equal(p.data.name,'札幌旅行');
 finish({days:[],payments:[]});await reload;assert.equal(p.data.error,'');
});

test('combined scope is deduplicated and ascending on every entry path',async()=>{
 let dates;
 const p=loadPage('day-bill',{getLedger:async()=>({days:['2026-10-29','2026-10-28'].map(date=>({date,status:'pending',pending:[],groups:[]})),payments:[]}),listExpenses:async()=>[],listTripMembers:async()=>[{id:'me',is_current:true},{id:'friend'}],previewPayment:async(t,scope)=>{dates=scope;return {dates:scope,groups:[]}}});
 await p.onLoad({tripId:'t',dates:encodeURIComponent('2026-10-29,2026-10-28,2026-10-29')});assert.deepEqual(Array.from(dates),['2026-10-28','2026-10-29']);assert.match(p.data.scopeMessage,/10月28日.*10月29日/);
 await p.onLoad({tripId:'t'});assert.deepEqual(Array.from(dates),['2026-10-28','2026-10-29']);
});

test('partial combined history displays only the selected allocation',()=>{
 const payment={currency:'CNY',amount:'30.00',from_participant_id:'b',to_participant_id:'a',allocations:[{date:'2026-10-28',amount:'10.00'},{date:'2026-10-29',amount:'20.00'}]};
 const scope=ui.scopePayment(payment,['2026-10-28'],['a','b']);assert.equal(scope.amount,'10.00');assert.equal(scope.combined,true);assert.equal(scope.from_participant_id,'b');
});

test('missing name fetch failure preserves bill and can recover on retry',async()=>{
 let fail=true;
 const p=loadPage('day-bill',{listTrips:async()=>{if(fail)throw Error('offline');return [{id:'t',name:'重试恢复的旅行'}]},getLedger:async()=>({days:[],payments:[]}),listExpenses:async()=>[],listTripMembers:async()=>[{id:'me',is_current:true}]});
 await p.onLoad({tripId:'t',date:'2026-10-29'});assert.equal(p.data.error,'');assert.equal(p.data.name,'旅行账本');fail=false;await p.reload();assert.equal(p.data.name,'重试恢复的旅行');
});

test('refreshing a bill blocks queued financial actions',async()=>{
 let calls=0;const p=loadPage('day-bill',{previewPayment:async()=>calls++,revokePayment:async()=>calls++});p.setData({loading:true,day:{status:'pending'},payments:[{id:'p'}]});await p.confirmPayment();await p.revoke({currentTarget:{dataset:{id:'p'}}});assert.equal(calls,0);
});

test('payer combined route decodes encoded dates before previewing',async()=>{
 const dates=['2026-09-29','2026-09-30'];let requested;
 const p=loadPage('day-bill',{getLedger:async()=>({members:['me','other'],days:dates.map(date=>({date,status:'pending',pending:[],groups:[]})),payments:[]}),listExpenses:async()=>[],listTripMembers:async()=>[{id:'me',is_current:true},{id:'other'}],previewPayment:async(t,d)=>{requested=d;if(!d.length)throw Error('请先手动结束当天记账');return {dates:d,groups:[{currency:'CNY',amount:'1793.75',from_participant_id:'me',to_participant_id:'other'}]}}});
 await p.onLoad({tripId:'t',dates:encodeURIComponent(dates.join(','))});assert.deepEqual(Array.from(requested),dates);assert.equal(p.data.error,'');assert.equal(p.data.canSettle,true);assert.match(p.data.paymentLabel,/我已付款/);
});
test('home keeps two directions visible and filtering never triggers empty creation',async()=>{
 const p=loadPage('trips',{listTrips:async()=>[{id:'t',name:'东京',member_count:2}],ledgerSummary:async()=>({trips:[{id:'t',day_count:1}],groups:[{currency:'CNY',receivable:'320',payable:'80'}]})});
 await p.loadTrips();assert.equal(p.data.summaryGroups[0].receivableText,'¥320.00');assert.equal(p.data.summaryGroups[0].payableText,'¥80.00');
 p.applyPreferences('date',true);assert.equal(p.data.trips.length,0);assert.equal(p.data.emptyReady,false);
});
test('today cannot invoke payment; payer uses mark-paid and recipient confirms',async()=>{
 const actions=[];const group={currency:'CNY',amount:'20',from_participant_id:'me',to_participant_id:'other'};
 const api={previewPayment:async()=>({groups:[group],dates:['2026-10-26'],token:'tok'}),markPaid:async()=>actions.push('marked'),markPayment:async()=>actions.push('confirmed')};
 const p=loadPage('day-bill',api);p.reload=async()=>{};p.setData({loading:false,tripId:'t',date:'2026-10-26',current:'me',labels:{me:'我',other:'小周'},day:{status:'recording'}});await p.confirmPayment();assert.equal(actions.length,0);
 p.setData({day:{status:'pending'}});await p.confirmPayment();assert.deepEqual(actions,['marked']);p.setData({current:'other'});await p.confirmPayment();assert.deepEqual(actions,['marked','confirmed']);
});
test('recipient copies complete bill and shares stable day route',()=>{
 const p=loadPage('day-bill',{});p.setData({tripId:'abc',date:'2026-10-26',name:'东京',title:'10月26日',expenseCount:3,original:'9,971 JPY',transfers:[{from:'小周',to:'我',amountText:'¥235.97'}]});p.copyBill({currentTarget:{dataset:{index:0}}});assert.match(p.calls[0],/东京.*小周付给我.*235.97.*3 笔/);assert.match(p.onShareAppMessage().path,/tripId=abc&date=2026-10-26/);
});
test('day reload presents payer marking without settling and puts me first',async()=>{
 const transfer={currency:'CNY',amount:'20.00',from_participant_id:'me',to_participant_id:'other'};
 const p=loadPage('day-bill',{getLedger:async()=>({days:[{date:'2026-10-26',status:'pending',pending:[transfer],groups:[{currency:'CNY',paid_by_participant:{},responsibility_by_participant:{}}]}],payments:[],payer_marks:[{id:'mark',dates:['2026-10-26'],currency:'CNY',amount:'20.00',from_participant_id:'me',to_participant_id:'other',marked_by:'me',marked_at:'2026-10-26T10:00:00Z'}]}),listExpenses:async()=>[],listTripMembers:async()=>[{id:'other',nickname:'小周'},{id:'me',is_current:true}]});
 await p.onLoad({tripId:'t',date:'2026-10-26'});assert.equal(p.data.payerWaiting,true);assert.equal(p.data.day.status,'pending');assert.equal(p.data.groupRows[0].members[0].name,'我');assert.match(p.data.paymentLabel,/等待确认/);
});
test('travel totals add individual conversion results and retain mixed currency originals',()=>{
 const record=(id,currency,amount,rate)=>({id,payload:{expenses:[{items:[{amount:{currency,amount}}],reference_rate:rate}]}});
 const totals=ui.expenseTotals([record('a','JPY','1280','0.04236'),record('b','JPY','2964','0.04236'),record('c','JPY','5727','0.04236')]);
 assert.equal(totals.original,'9,971 JPY');assert.equal(totals.settlement,'¥422.38');
});

test('incomplete accounting is never presented as settled or as a settlement date',()=>{
 const trip={name:'东京',member_count:2};
 assert.equal(ui.tripCard(trip,{day_count:2,incomplete:true}).state,'invalid');
 assert.equal(ui.tripCard(trip,{day_count:2,latest_expense_date:'2026-10-01'}).statusNote,'');
 assert.equal(ui.tripCard(trip,{day_count:2,settled_at:'2026-10-05T01:00:00Z'}).statusNote,'10/05 结清');
});

test('trip avatar strips date prefixes and undated solo trips never show zero days',()=>{
 assert.equal(ui.tripCard({name:'202610札幌',member_count:2},{day_count:2}).avatar,'札');
 assert.doesNotMatch(ui.tripCard({name:'新旅行',member_count:1},{}).subtitle,/0 天/);
 assert.equal(ui.tripCard({name:'账本',member_count:2},{_unverified:true}).statusText,'同步中');
});

test('cross-year date titles are explicit',()=>{
 assert.match(ui.dateTitle('2024-11-26','2026-10-28'),/2024年11月26日/);
 assert.doesNotMatch(ui.dateTitle('2026-10-04','2026-10-28'),/2026年/);
});

test('cancelling payment confirmation never starts submission loading',async()=>{
 let page,observedBusy,calls=0;
 const api={previewPayment:async()=>({dates:['2026-10-07'],token:'t',groups:[{currency:'CNY',amount:'10',from_participant_id:'friend',to_participant_id:'me'}]}),markPayment:async()=>calls++};
 page=loadPage('day-bill',api,{showModal(o){observedBusy=page.data.busy;o.success({confirm:false})}});page.reload=async()=>{};
 page.setData({loading:false,tripId:'t',date:'2026-10-07',current:'me',labels:{me:'我',friend:'小周'},day:{status:'pending'}});
 await page.confirmPayment();assert.equal(observedBusy,false);assert.equal(page.data.busy,false);assert.equal(page.data.confirming,false);assert.equal(calls,0);
});

test('settled combined scope keeps payment history and included dates',async()=>{
 const dates=['2026-10-06','2026-10-07'],payment={id:'p',created_by:'me',created_at:'2026-10-28T14:00:00Z',currency:'CNY',amount:'238.33',from_participant_id:'friend',to_participant_id:'me',allocations:[{date:dates[0],amount:'2.36'},{date:dates[1],amount:'235.97'}]};
 const p=loadPage('day-bill',{getLedger:async()=>({members:['friend','me'],days:dates.map(date=>({date,status:'settled',groups:[],pending:[]})),payments:[payment]}),listExpenses:async()=>[],listTripMembers:async()=>[{id:'me',is_current:true},{id:'friend',nickname:'小周'}],previewPayment:async()=>({dates,groups:[]})});
 await p.onLoad({tripId:'t',dates:dates.join(',')});assert.equal(p.data.transfers.length,1);assert.equal(p.data.transfers[0].amountText,'¥238.33');assert.equal(p.data.transfers[0].settled,true);assert.match(p.data.scopeMessage,/10月6日.*10月7日/);assert.equal(p.data.transferTitle,'收款');assert.equal(p.data.canSettle,false);
});

test('greeting uses current entry time rather than the cached page greeting',()=>{
 assert.equal(ui.greeting(new Date('2026-10-28T22:42:00')),'晚上好');assert.equal(ui.greeting(new Date('2026-10-28T12:00:00')),'中午好');
 const p=loadPage('trips',{});p.setData({greeting:'中午好',hasLoaded:false});p.onShow();assert.equal(p.data.greeting,ui.greeting());
});

test('revocation restores both home totals and the trip card balance',async()=>{
 let paid=true;const payment={id:'p',created_at:'2026-10-28T10:00:00Z',created_by:'me',currency:'CNY',amount:'30.00',from_participant_id:'friend',to_participant_id:'me',allocations:[{date:'2026-10-07',amount:'-30.00'}]},transfer={currency:'CNY',amount:'30.00',from_participant_id:'friend',to_participant_id:'me'};
 const api={listTrips:async()=>[{id:'t',name:'东京',member_count:2}],listTripMembers:async()=>[{id:'me',is_current:true},{id:'friend',nickname:'小周'}],listExpenses:async()=>[],getLedger:async()=>({members:['friend','me'],days:[{date:'2026-10-07',status:paid?'settled':'pending',groups:[],pending:paid?[]:[transfer]}],payments:[{...payment,...(!paid?{revoked_at:'now'}:{})}]}),revokePayment:async()=>{paid=false},ledgerSummary:async()=>({trips:[{id:'t',day_count:1,pending:paid?[]:[{...transfer,direction:'receivable'}]}],groups:[{currency:'CNY',receivable:paid?'0':'30',payable:'0'}]})};
 const day=loadPage('day-bill',api),home=loadPage('trips',api);await day.onLoad({tripId:'t',date:'2026-10-07'});await home.loadTrips();assert.equal(home.data.trips[0].state,'settled');
 await day.revoke({currentTarget:{dataset:{id:'p'}}});await home.loadTrips();assert.equal(day.data.day.status,'pending');assert.equal(home.data.trips[0].statusText,'¥30.00');assert.equal(home.data.summaryGroups[0].receivableText,'¥30.00');
});

 test('member refresh changes names while avatar colors follow stable identity',async()=>{
 let nickname='小周';let reverse=false;const transfer=()=>({currency:'CNY',amount:'10',from_participant_id:reverse?'me':'friend',to_participant_id:reverse?'friend':'me'});
 const p=loadPage('day-bill',{getLedger:async()=>({members:['friend','me'],days:[{date:'2026-10-07',status:'pending',groups:[],pending:[transfer()]}],payments:[]}),listExpenses:async()=>[],listTripMembers:async()=>[{id:'me',is_current:true},{id:'friend',nickname}]});
 await p.onLoad({tripId:'t',date:'2026-10-07'});assert.equal(p.data.transfers[0].fromIsCurrent,false);assert.equal(p.data.transfers[0].toIsCurrent,true);assert.equal(p.data.transfers[0].fromAvatar,'小');
 nickname='同行新昵称';reverse=true;await p.onShow();assert.equal(p.data.transfers[0].to,'同行新昵称');assert.equal(p.data.transfers[0].fromIsCurrent,true);assert.equal(p.data.transfers[0].toIsCurrent,false);
 });

 test('settled source bill exposes the destination of its changed balance',async()=>{
 const p=loadPage('day-bill',{getLedger:async()=>({currency:'CNY',members:['friend','me'],days:[{date:'2026-10-07',status:'settled',groups:[],pending:[]},{date:'2026-10-08',status:'pending',groups:[],pending:[],carry_entries:[{source_date:'2026-10-07',currency:'CNY',amount:'1.00'}]}],payments:[]}),listExpenses:async()=>[],listTripMembers:async()=>[{id:'me',is_current:true},{id:'friend',nickname:'小周'}]});
 await p.onLoad({tripId:'t',date:'2026-10-07'});assert.equal(p.data.carriedTo.length,1);assert.equal(p.data.carriedTo[0].date,'2026-10-08');assert.equal(p.data.carriedTo[0].amountText,'¥1.00');
 });

 test('combined share scope follows server normalization before matching payer marks',async()=>{
 const group={currency:'CNY',amount:'10',from_participant_id:'friend',to_participant_id:'me'};
 const p=loadPage('day-bill',{getLedger:async()=>({members:['friend','me'],days:[{date:'2026-10-06',status:'settled',groups:[],pending:[]},{date:'2026-10-07',status:'pending',groups:[],pending:[group]}],payments:[],payer_marks:[{...group,id:'mark',dates:['2026-10-07'],marked_by:'friend'}]}),listExpenses:async()=>[],listTripMembers:async()=>[{id:'me',is_current:true},{id:'friend'}],previewPayment:async()=>({dates:['2026-10-07'],groups:[group]})});
 await p.onLoad({tripId:'t',dates:'2026-10-06,2026-10-07'});assert.deepEqual(Array.from(p.data.scopeDates),['2026-10-07']);assert.equal(p.data.transfers[0].marked,true);assert.doesNotMatch(p.data.scopeMessage,/10月6日/);
 });
