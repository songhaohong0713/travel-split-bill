function today() { const d=new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}` }
function inProgress(trip, now=today()) {
  if (trip.start_date || trip.end_date) return Boolean(trip.start_date && trip.end_date && trip.start_date<=now && trip.end_date>=now)
  const delta=(Date.parse(now)-Date.parse(trip.latest_expense_date))/86400000
  return delta>=0 && delta<=2
}
function symbol(currency) { return ({CNY:'¥',JPY:'¥',USD:'$',KRW:'₩'})[currency] || '' }
function dateTitle(value) { const d=new Date(`${value}T12:00:00`); return `${d.getMonth()+1}月${d.getDate()}日 周${'日一二三四五六'[d.getDay()]}` }
function tripCard(trip, detail={}, now=today()) {
  const dates=trip.start_date ? `${trip.start_date.slice(5).replace('-','/')}${trip.end_date ? '–'+trip.end_date.slice(5).replace('-','/') : ''}` : ''
  const pending=(detail.pending || []).filter(p=>!p.offset_only && Number(p.amount)>0)
  return {...trip,...detail,subtitle:[dates,`${detail.day_count || 0} 天`,`成员 ${trip.member_count || 1}/2`].filter(Boolean).join(' · '),statusText:pending.length ? '待结清 '+pending.map(p=>`${symbol(p.currency)}${p.amount}${pending.length>1 ? ' '+p.currency : ''}`).join(' / ') : detail.has_recording ? '记录中' : '已结清',hasPending:pending.length>0,ongoing:inProgress({...trip,latest_expense_date:detail.latest_expense_date},now)}
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
function originalTotals(records) {
  const sums={}
  for(const r of records) for(const bill of (r.payload || {}).expenses || []) {
    for(const item of bill.items || []) { const m=item.amount; if(m) sums[m.currency]=(sums[m.currency] || 0)+Math.round(Number(m.amount)*100); if(!item.tax_included && item.tax_amount) {const t=item.tax_amount; sums[t.currency]=(sums[t.currency] || 0)+Math.round(Number(t.amount)*100)} }
    for(const a of bill.adjustments || []) {const m=a.amount; if(m) sums[m.currency]=(sums[m.currency] || 0)+Math.round(Number(m.amount)*100)}
  }
  return Object.entries(sums).map(([c,v])=>`${symbol(c)}${(v/100).toFixed(['JPY','KRW'].includes(c)?0:2)} ${c}`).join(' / ')
}
function dayPayment(payment,date,members) {
  if(!date)return payment
  const allocation=(payment.allocations || []).find(a=>a.date===date)
  if(!allocation)return payment
  const signed=Number(allocation.amount), ids=members.slice().sort()
  return {...payment,amount:Math.abs(signed).toFixed(['JPY','KRW'].includes(payment.currency)?0:2),from_participant_id:signed>=0?ids[1]:ids[0],to_participant_id:signed>=0?ids[0]:ids[1],combined:(payment.allocations || []).length>1}
}
module.exports={today,inProgress,symbol,dateTitle,tripCard,tripUrl,confirm,newExpense,originalTotals,dayPayment}
