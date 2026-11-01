function today() { const d=new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}` }
function inProgress(trip, now=today()) {
  return Boolean(trip.start_date && trip.end_date && trip.start_date<=now && trip.end_date>=now)
}
function money() { return require('./money') }
function symbol(currency) { return currency==='CNY'?'¥':'' }
function greeting(now=new Date()){const h=now.getHours();return h<11?'早上好':h<13?'中午好':h<18?'下午好':'晚上好'}
function dateTitle(value,now=today()) { const d=new Date(value+'T12:00:00'); return (String(value).slice(0,4)!==String(now).slice(0,4)?d.getFullYear()+'年':'')+(d.getMonth()+1)+'月'+d.getDate()+'日 周'+'日一二三四五六'[d.getDay()] }
function tripCard(trip, detail={}, now=today()) {
  const dates=trip.start_date ? trip.start_date.slice(5).replace('-','/')+(trip.end_date?'–'+trip.end_date.slice(5).replace('-','/'):'') : ''
  const pending=(detail.pending || []).filter(p=>!p.offset_only && Number(p.amount)>0)
  const durationDays=trip.start_date && trip.end_date ? Math.max(1,Math.floor((Date.parse(trip.end_date)-Date.parse(trip.start_date))/86400000)+1) : Number(detail.day_count || 0)
  const state=Number(trip.member_count || 1)<2?'invite':detail._unverified?'unknown':detail.incomplete?'invalid':pending.length?'pending':detail.has_recording?'recording':Number(detail.day_count || 0)>0 || detail.latest_expense_date?'settled':'empty'
  return {...trip,...detail,durationDays,avatar:Array.from((trip.name || '').replace(/^[\d\s/_·-]+/,'') || '旅')[0],state,subtitle:[dates,trip.member_count>1?'我和'+(detail.companion_name || trip.companion_name || '同行人'):(durationDays?durationDays+' 天':'只有你一人')].filter(Boolean).join(' · '),statusText:state==='unknown'?'同步中':state==='invite'?'去邀请':state==='invalid'?'需补全':pending.length?pending.map(p=>money().formatMoney(p.currency,p.amount)).join(' / '):state==='recording'?'记录中':state==='empty'?'还没有消费':'已结清',statusNote:state==='invite'?'只有你一人':pending.length?(detail.direction || (pending[0].direction==='receivable'?(trip.companion_name || '同行人')+'待付你':pending[0].direction==='payable'?'你待付'+(trip.companion_name || '同行人'):'待结清')):state==='settled' && detail.settled_at?detail.settled_at.slice(5,10).replace('-','/')+' 结清':'',hasPending:pending.length>0,direction:pending[0]?.direction || '',ongoing:inProgress(trip,now)}
}
function sortTrips(cards, sort='date', hideSettled=false) {
  return cards.filter(t=>!hideSettled || t.state!=='settled').slice().sort((a,b)=>{
    if(sort==='pending' && a.hasPending!==b.hasPending)return a.hasPending?-1:1
    const key=sort==='recent'?'latest_expense_date':'start_date'
    return String(b[key] || b.created_at || '').localeCompare(String(a[key] || a.created_at || ''))
  })
}
function summaryGroups(groups) {
  return groups.map(g=>({...g,receivableText:money().formatMoney(g.currency,g.receivable || '0'),payableText:money().formatMoney(g.currency,g.payable || '0')}))
}
function tripUrl(trip, extra='') { return `/pages/trip-detail/index?tripId=${encodeURIComponent(trip.id)}&name=${encodeURIComponent(trip.name)}&currency=${trip.default_currency}&isOwner=${trip.is_owner ? '1':'0'}${extra}` }
function confirm(content,title='请确认') { return new Promise(resolve=>wx.showModal({title,content,success:r=>resolve(r.confirm),fail:()=>resolve(false)})) }
function newExpense(trip) {
  wx.showActionSheet({itemList:['拍小票','手动输入'],success:({tapIndex})=>{
    const url=`/pages/expense/index?tripId=${encodeURIComponent(trip.id)}&currency=${trip.default_currency}`
    if(tapIndex===1) return wx.navigateTo({url})
    wx.chooseMedia({count:1,mediaType:['image'],sourceType:['camera','album'],success:({tempFiles})=>{
      getApp().globalData.pendingReceiptPath=tempFiles[0].tempFilePath
      wx.navigateTo({url})
    }})
  }})
}
function addAmounts(values,currency='CNY') { return money().sum(values,currency) }
function expenseTotals(records, currency='CNY', labels={}) {
  const views=records.map(r=>money().expenseView(r,currency,labels)), groups={},settlements={}
  for(const view of views){
    for(const [c,value] of Object.entries(view.originalTotals || {[view.currency]:view.originalAmount}))(groups[c] ||= []).push(value)
    for(const [c,value] of Object.entries(view.settlementTotals || {[view.settlementCurrency]:view.settlementAmount}))(settlements[c] ||= []).push(value)
  }
  const text=values=>Object.entries(values).map(([c,amounts])=>money().formatMoney(c,addAmounts(amounts,c))).join(' + ')
  return {count:views.length,views,original:text(groups),settlement:views.some(v=>v.incomplete)?'汇率待补全':text(settlements) || money().formatMoney(currency,'0')}
}
function originalTotals(records) { return expenseTotals(records).original }
function scopePayment(payment,dates,members){
 const selected=(payment.allocations || []).filter(a=>dates.includes(a.date));
 if(selected.length===(payment.allocations || []).length)return {...payment,combined:false};
 const signed=money().units(money().sum(selected.map(a=>a.amount),payment.currency),payment.currency),ids=members.slice().sort();
 return {...payment,amount:money().fromUnits(signed<0n?-signed:signed,payment.currency),from_participant_id:signed>=0n?ids[1]:ids[0],to_participant_id:signed>=0n?ids[0]:ids[1],combined:true};
}
function dayPayment(payment,date,members) {
  if(!date)return payment
  const allocation=(payment.allocations || []).find(a=>a.date===date)
  if(!allocation)return payment
  const signed=Number(allocation.amount), ids=members.slice().sort()
  return {...payment,amount:Math.abs(signed).toFixed(['JPY','KRW'].includes(payment.currency)?0:2),from_participant_id:signed>=0?ids[1]:ids[0],to_participant_id:signed>=0?ids[0]:ids[1],combined:(payment.allocations || []).length>1}
}
async function createTrip(){
    const api=require('./api')
    const ui=module.exports
    if(this.data.creating)return
    const name=this.data.name.trim();if(this.lastCreateName!==name || this.lastCreateCurrency!==this.data.currency){this.createdTrip=null;this.createRequestId=null;this.lastCreateName=name;this.lastCreateCurrency=this.data.currency}
    if(!name)return this.setData({error:'请输入旅行名称'})
    this.setData({creating:true,error:''})
    try{
      // Creation is also available offline.
      let trips
      try { trips=await api.listTrips() } catch(e) { trips=require('./offline-trips').listCachedTrips() }
      if(trips.some(t=>t.name.trim()===name) && !await ui.confirm('已有同名旅行，仍要创建吗？','同名旅行'))return
      const trip=this.createdTrip || await api.createTrip(name,this.data.currency,this.createRequestId || (this.createRequestId='xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g,c=>{const n=Math.floor(Math.random()*16);return(c==='x'?n:(n&3)|8).toString(16)})))
      let dateFailed=false
      if(this.data.startDate || this.data.endDate){
        try{await api.updateTripDates(trip.id,{start_date:this.data.startDate || null,end_date:this.data.endDate || null})}
        catch(e){dateFailed=true}
      }
      this.createdTrip=trip;wx.redirectTo({url:ui.tripUrl({...trip,name,is_owner:true},'&inviteGuide=1'+(dateFailed?'&dateFailed=1':''))})
    }catch(e){this.setData({error:e.message || '创建失败，请重试'})}
    finally{this.setData({creating:false})}
  }
module.exports={scopePayment,greeting,expenseTotals,addAmounts,createTrip,sortTrips,summaryGroups,today,inProgress,symbol,dateTitle,tripCard,tripUrl,confirm,newExpense,originalTotals,dayPayment}
