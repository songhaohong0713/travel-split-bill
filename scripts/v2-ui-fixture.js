// DevTools-only synthetic fixture; never imported by the application.
async function installV2Fixture() {
 const api=require('services/api.js'),form=require('services/expense-form.js'),money=require('services/money.js');
 const state={current:'a',trips:[{id:'v2-demo',name:'东京',default_currency:'CNY',member_count:2,is_owner:true,start_date:'2026-10-25',end_date:'2026-10-29'}],records:[],marks:[],payments:[]};
 getApp().globalData.v2Fixture=state;
 const members=()=>[{id:'a',nickname:'小林',is_current:state.current==='a',is_owner:true},{id:'b',nickname:'小周',is_current:state.current==='b',is_owner:false}];
 for(const [id,title,amount] of [['1','药妆店','1280'],['2','小破化妆品店','2964'],['3','DHC 油','5727']])state.records.push({id,trip_id:'v2-demo',occurred_at:'2026-10-25',revision:1,is_creator:true,payload:form.buildPayload({tripId:'v2-demo',title,amount,currency:'JPY',settlementCurrency:'CNY',referenceRate:'0.04236',payer:'a',memberOptions:members(),splitMode:'even',items:[],adjustments:[]})});
 const records=id=>state.records.filter(r=>r.trip_id===id);
 function group(rs){const totals=rs.map(r=>money.expenseView(r,'CNY').settlementAmount),total=money.sum(totals),half=money.sum(rs.map(r=>form.previewRows({...form.hydrate(r),memberOptions:members().map(m=>({...m,label:m.nickname}))})[1].convertedAmount));return {currency:'CNY',paid_by_participant:{a:{amount:total},b:{amount:'0.00'}},responsibility_by_participant:{a:{amount:money.fromUnits(money.units(total,'CNY')-money.units(half,'CNY'),'CNY')},b:{amount:half}}}}
 const transfer=rs=>({currency:'CNY',amount:group(rs).responsibility_by_participant.b.amount,from_participant_id:'b',to_participant_id:'a'});
 const settled=date=>state.payments.some(p=>!p.revoked_at&&p.allocations.some(a=>a.date===date));
 api.login=api.ensureAuthenticated=async()=>{};
 api.listTrips=async()=>state.trips;
 api.listTripMembers=async id=>members().slice(0,state.trips.find(t=>t.id===id)?.member_count||2);
 api.listExpenses=async id=>records(id);
 api.getExchangeRate=async()=>({rate:'0.04236',provider:'验收样例',effective_date:'2026-10-25'});
 api.createTrip=async(name,currency)=>{const trip={id:'v2-new',name,default_currency:currency,member_count:1,is_owner:true};state.trips.push(trip);return trip};
 api.updateTripDates=async(id,dates)=>Object.assign(state.trips.find(t=>t.id===id),dates);
 api.createTripInvite=async()=>({token:'v2-synthetic-invite'});
 api.getTripInvite=async()=>({trip_id:'v2-demo',name:'东京',default_currency:'CNY',inviter_name:'小林',expires_at:'2026-11-01T12:00:00Z',already_member:false});
 api.acceptTripInvite=async()=>({trip_id:'v2-demo'});
 api.getLedger=async id=>{const rs=records(id),dates=[...new Set(rs.map(r=>r.occurred_at))],today=new Date().toISOString().slice(0,10);const days=dates.map(date=>{const list=rs.filter(r=>r.occurred_at===date),status=settled(date)?'settled':date>=today?'recording':'pending';return {date,status,count:list.length,pending:status==='pending'?[transfer(list)]:[],groups:[group(list)],carry_entries:[]}});return {members:['a','b'],days,summary:days.flatMap(d=>d.pending),payments:state.payments,payer_marks:state.marks,history_status:'current',expense_summaries:[]}};
 api.ledgerSummary=async()=>{const trips=[];let recv='0',pay='0';for(const t of state.trips){const ledger=await api.getLedger(t.id),pending=ledger.summary.map(p=>({...p,direction:state.current==='a'?'receivable':'payable'}));if(state.current==='a')recv=money.sum([recv,...pending.map(p=>p.amount)]);else pay=money.sum([pay,...pending.map(p=>p.amount)]);trips.push({id:t.id,day_count:ledger.days.length,latest_expense_date:ledger.days[0]?.date,pending,has_recording:ledger.days.some(d=>d.status==='recording')})}return {trips,groups:[{currency:'CNY',receivable:recv,payable:pay}]}};
 api.previewPayment=async(id,dates)=>{const ledger=await api.getLedger(id),ds=dates||ledger.days.filter(d=>d.status==='pending').map(d=>d.date),rs=records(id).filter(r=>ds.includes(r.occurred_at));return {token:'synthetic-v2-token',dates:ds,groups:rs.length&&ds.some(d=>!settled(d))?[transfer(rs)]:[]}};
 api.markPaid=async(id,p)=>{const g=(await api.previewPayment(id,p.dates)).groups[0];state.marks.push({...g,id:'v2-mark',dates:p.dates,marked_by:state.current,marked_at:new Date().toISOString()})};
 api.markPayment=async(id,p)=>{const g=(await api.previewPayment(id,p.dates)).groups[0];state.payments.push({...g,id:'v2-payment',created_by:state.current,created_at:new Date().toISOString(),allocations:p.dates.map(date=>({date,amount:g.amount}))});state.marks.forEach(m=>m.confirmed_payment_id='v2-payment')};
 api.revokePayment=async(id,pid)=>{Object.assign(state.payments.find(p=>p.id===pid),{revoked_at:new Date().toISOString(),revoked_by:state.current})};
 api.createExpense=async(id,date,payload)=>{const r={id:'v2-saved',trip_id:id,occurred_at:date,payload,revision:1,is_creator:true};state.records.push(r);return r};
 api.updateExpense=async(id,rid,rev,date,payload)=>Object.assign(state.records.find(r=>r.id===rid),{payload,occurred_at:date,revision:rev+1});
 const page=getCurrentPages().slice(-1)[0];if(page?.route==='pages/trips/index')await page.loadTrips();
 return {synthetic:true,trips:state.trips.length,expenses:state.records.length};
}
