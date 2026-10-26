// DevTools-only synthetic fixture. Never imported by the mini program.
async function installRoundTwoFixture() {
  const api=require('services/api.js')
  const state={trips:[{id:'demo-tokyo',name:'东京',default_currency:'CNY',member_count:2,is_owner:true,start_date:'2026-10-24',end_date:'2026-10-28'}],records:[],payments:[],closed:[],current:'a'}
  getApp().globalData.roundTwoFixture=state
  const day='2026-10-25'
  function pending(){return state.records.reduce((s,r)=>s+Number(r.payload.expenses[0].items.reduce((s,i)=>s+Number(i.amount.amount),0))/2,0)}
  api.login=api.ensureAuthenticated=async()=>{}
  api.listTrips=async()=>state.trips
  api.listTripMembers=async(id)=>[{id:'a',nickname:'同行人',is_current:state.current==='a',is_owner:true},{id:'b',nickname:'同行人',is_current:state.current==='b'}].slice(0,(state.trips.find(t=>t.id===id) || {}).member_count || 2)
  api.createTrip=async(name,currency)=>{const t={id:'demo-new',name,default_currency:currency,member_count:1,is_owner:true};state.trips.push(t);return t}
  api.updateTripDates=async(id,d)=>Object.assign(state.trips.find(t=>t.id===id),d)
  api.createTripInvite=async()=>({token:'synthetic-invite-only'})
  api.listExpenses=async()=>state.records
  api.createExpense=async(id,date,payload)=>{const r={id:'demo-expense',trip_id:id,occurred_at:date,payload,revision:1,is_creator:true};state.records.push(r);return r}
  api.closeDay=async(id,date)=>state.closed.push(date)
  api.getLedger=async()=>{
    const paid=state.payments.some(p=>!p.revoked_at),value=pending().toFixed(2),date=state.records[0]?.occurred_at || day
    const transfer={currency:'CNY',amount:value,signed_amount:value,from_participant_id:'b',to_participant_id:'a'}
    const groups=[{currency:'CNY',paid_by_participant:{a:{amount:(pending()*2).toFixed(2)},b:{amount:'0.00'}},responsibility_by_participant:{a:{amount:value},b:{amount:value}}}]
    return {members:['a','b'],summary:state.records.length && !paid?[transfer]:[],payments:state.payments,days:state.records.length?[{date,status:paid?'settled':'pending',count:state.records.length,pending:paid?[]:[transfer],groups,carry_entries:[]}]:[]}
  }
  api.ledgerSummary=async()=>({trips:state.trips.map(t=>({id:t.id,day_count:state.records.length?1:0,latest_expense_date:day,pending:state.records.length && !state.payments.some(p=>!p.revoked_at)?[{currency:'CNY',amount:pending().toFixed(2),direction:'receivable'}]:[],has_recording:false})),groups:state.records.length && !state.payments.some(p=>!p.revoked_at)?[{currency:'CNY',receivable:pending().toFixed(2),payable:'0.00'}]:[]})
  api.previewPayment=async(id,dates)=>({token:'synthetic-token',dates:dates || [state.records[0]?.occurred_at || day],groups:[{currency:'CNY',amount:pending().toFixed(2),from_participant_id:'b',to_participant_id:'a'}]})
  api.markPayment=async()=>state.payments.push({id:'demo-payment',created_by:state.current,created_at:new Date().toISOString(),currency:'CNY',amount:pending().toFixed(2),from_participant_id:'b',to_participant_id:'a',allocations:[{date:state.records[0].occurred_at,amount:pending().toFixed(2)}]})
  api.revokePayment=async(id,payment)=>Object.assign(state.payments.find(p=>p.id===payment),{revoked_at:new Date().toISOString(),revoked_by:state.current})
  const p=getCurrentPages().slice(-1)[0];if(p && p.route==='pages/trips/index')await p.loadTrips()
  return {synthetic:true,tripCount:state.trips.length}
}
