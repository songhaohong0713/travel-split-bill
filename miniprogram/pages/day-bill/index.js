const api=require('../../services/api')
const ui=require('../../services/ledger-view')
const {memberLabelMap}=require('../../services/member-labels')
const money=require('../../services/money')
const offline=require('../../services/offline-trips')
const orderedDates=dates=>Array.from(new Set(dates)).sort()
function decodeQuery(value){try{return decodeURIComponent(String(value || ''))}catch(_){return String(value || '')}}
function timeLabel(value){if(!value)return '';const d=new Date(value);const day=`${d.getMonth()+1}/${d.getDate()}`;const today=new Date();return `${d.toDateString()===today.toDateString()?'今天':day} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`}
Page({
 data:{expenseCount:0,transferCount:0,hasGroups:false,tripId:'',date:'',all:false,name:'',loading:true,busy:false,confirming:false,allSettled:false,loadedView:false,transferTitle:'转账',error:'',day:null,expenses:[],payments:[],pending:[],groupRows:[],previewGroups:[],scopeDates:[],scopeMessage:'',transfers:[],carry:[]},
 onLoad(q){this.selectedDates=q.date?[q.date]:q.dates?orderedDates(decodeQuery(q.dates).split(',').filter(d=>/^\d{4}-\d{2}-\d{2}$/.test(d))):null;if(this.selectedDates&&!this.selectedDates.length)this.selectedDates=null;if(wx.setNavigationBarTitle)wx.setNavigationBarTitle({title:q.date?'当天账单':'合并结清'});this.setData({tripId:q.tripId,date:q.date || '',all:!q.date,name:decodeQuery(q.name),title:q.date?ui.dateTitle(q.date):'合并结清',loadedView:true});return this.reload()},
 onShow(){if(this.loaded)return this.reload()},
 async resolveTripName(){
  if(this.data.name && this.data.name!=='旅行账本')return this.data.name
  const cached=offline.listCachedTrips().find(t=>t.id===this.data.tripId)
  if(cached?.name)return cached.name
  if(typeof api.listTrips!=='function')return ''
  try{return ((await api.listTrips()).find(t=>t.id===this.data.tripId)||{}).name || ''}catch(_){return ''}
 },
 async reload(){
  const requestId=(this.loadSequence||0)+1;this.loadSequence=requestId
  this.setData({loading:true,canSettle:false,payerWaiting:false,error:'',previewGroups:[],scopeDates:[],transfers:[],pending:[],scopeMessage:''})
  try{
   const [ledger,records,members,tripName]=await Promise.all([api.getLedger(this.data.tripId),api.listExpenses(this.data.tripId),api.listTripMembers(this.data.tripId),this.resolveTripName()])
   if(requestId!==this.loadSequence)return
   for(const record of records)record.ledger_summary=(ledger.expense_summaries || []).find(x=>x.id===record.id)
   const labels=memberLabelMap(members)
   const current=(members.find(m=>m.is_current) || {}).id
   const ordered=members.slice().sort((a,b)=>Number(!!b.is_current)-Number(!!a.is_current))
   const day=ledger.days.find(d=>d.date===this.data.date) || null
   const closed=ledger.days.filter(d=>d.status!=='recording')
   if(this.data.all&&!this.selectedDates){const pendingDates=closed.filter(d=>d.status==='pending').map(d=>d.date);this.selectedDates=orderedDates(pendingDates.length?pendingDates:closed.map(d=>d.date))}
   const scope=this.data.all?this.selectedDates:[this.data.date]
   const filtered=records.filter(r=>this.data.all?scope.includes(r.occurred_at):r.occurred_at===this.data.date)
   const expenses=filtered.map(r=>({...money.expenseView(r,'CNY',labels),date:r.occurred_at}))
   const pending=(day && day.pending || []).map(p=>({...p,text:`${labels[p.from_participant_id] || '同行人'} 付给 ${labels[p.to_participant_id] || '同行人'}`}))
   const groups=day && day.groups || []
   const groupRows=groups.map(g=>({currency:g.currency,members:ordered.map(m=>({id:m.id,name:labels[m.id],paid:money.formatMoney(g.currency,(g.paid_by_participant[m.id] || {}).amount || '0'),responsibility:money.formatMoney(g.currency,(g.responsibility_by_participant[m.id] || {}).amount || '0')}))}))
   const payments=ledger.payments.filter(p=>(p.allocations || []).some(a=>scope.includes(a.date))).map(p=>({...p,by:labels[p.created_by] || '同行人',time:timeLabel(p.created_at),direction:`${labels[p.from_participant_id] || '同行人'} → ${labels[p.to_participant_id] || '同行人'}`})).reverse()
   const active=payments.filter(p=>!p.revoked_at)
   const transferView=p=>{
    const mark=(ledger.payer_marks || []).find(m=>!m.revoked_at && !m.confirmed_payment_id && !m.stale && m.currency===p.currency && m.from_participant_id===p.from_participant_id && m.to_participant_id===p.to_participant_id && Number(m.amount)===Number(p.amount) && (m.dates || []).length===scope.length && scope.every(d=>(m.dates || []).includes(d)))
    const recipient=!!p.offset_only || p.to_participant_id===current
    return {...p,viewKey:p.id || p.currency,amountText:money.formatMoney(p.currency,p.amount),recipient,marked:!!mark,markId:mark && mark.id,markText:mark?(labels[mark.marked_by] || '付款方')+' '+timeLabel(mark.marked_at)+' 标记已付款，等待收款确认':'',from:labels[p.from_participant_id] || '同行人',to:labels[p.to_participant_id] || '同行人',fromAvatar:Array.from(labels[p.from_participant_id] || '同行人').slice(-1)[0],toAvatar:Array.from(labels[p.to_participant_id] || '同行人').slice(-1)[0]}
   }
   const transfers=(day && day.status==='settled'?active.map(p=>({...ui.dayPayment(p,this.data.date,ledger.members || members.map(m=>m.id)),settled:true})):pending).map(transferView)
   const compatibilityError=ledger.compatibilityError || '';const actionable=transfers.filter(t=>!t.settled && (t.recipient || !t.marked));const action=actionable[0];this.setData({name:tripName || this.data.name || '旅行账本',memberCount:members.length,compatibilityError,hasTransfers:transfers.length>0,transferTitle:transfers.length?(transfers[0].recipient?'收款':'付款'):'转账',allSettled:false,current,day,labels,expenses,expenseCount:expenses.length,payments,hasActivePayments:active.length>0,groupRows,hasGroups:groupRows.length>0,pending,transfers,transferCount:transfers.length,hasCarry:!!(day && day.carry_entries && day.carry_entries.length),carry:day && day.carry_entries || [],title:this.data.all?'合并结清':this.data.date?ui.dateTitle(this.data.date):'当天账单',original:ui.originalTotals(filtered),statusLabel:day?{recording:'记录中',pending:pending.some(p=>p.to_participant_id===current)?'待收款':'待付款',settled:'已结清',invalid:'需补全'}[day.status]:'',billCurrency:groupRows.length===1?groupRows[0].currency:'',canSettle:!compatibilityError && !!(day && day.status==='pending' && actionable.length),paymentLabel:actionable.length>1?'处理 '+actionable.length+' 笔待结清':action?(action.recipient?'确认已收款 ':'我已付款 ')+action.amountText:transfers.length?'已标记付款，等待确认':'',payerWaiting:transfers.length>0 && actionable.length===0 && day?.status!=='settled',loading:this.data.all,historyPending:ledger.history_status==='pending'})
   this.loaded=true
   if(this.data.all){
    if(!closed.length){this.setData({scopeMessage:'还没有待结清日期',loading:false});return}
    const p=await api.previewPayment(this.data.tripId,scope)
    if(requestId!==this.loadSequence)return
    const history=active.map(payment=>({...ui.scopePayment(payment,scope,ledger.members || members.map(m=>m.id)),settled:true})).map(transferView)
    const allTransfers=[...p.groups.map(transferView),...history.filter(t=>!p.groups.some(g=>g.currency===t.currency))],allActions=allTransfers.filter(t=>!t.settled && (t.recipient || !t.marked));this.setData({loading:false,allSettled:p.groups.length===0,transferTitle:allTransfers.length?(allTransfers[0].recipient?'收款':'付款'):'转账',hasTransfers:allTransfers.length>0,payerWaiting:p.groups.length>0 && !allActions.length,previewGroups:p.groups,scopeDates:scope,transferCount:allTransfers.length,transfers:allTransfers,canSettle:!compatibilityError && allActions.length>0,paymentLabel:allActions.length>1?'处理 '+allActions.length+' 笔待结清':allActions.length?(allActions[0].offset_only?'确认抵扣结清 ':allActions[0].recipient?'确认已收款 ':'我已付款 ')+allActions[0].amountText:allTransfers.length?'已标记付款，等待确认':'',scopeMessage:'包含：'+scope.map(d=>ui.dateTitle(d)).join('、')})
   }
  }catch(e){if(requestId!==this.loadSequence)return;this.setData({loading:false,error:e.message || '账单暂时无法加载，请重试',transfers:[],previewGroups:[],scopeDates:[]})}
 },
 async confirmPayment(){
  if(this.data.loading || this.data.compatibilityError || this.data.confirming || this.data.busy || this.data.payerWaiting || (this.data.day && this.data.day.status==='recording'))return
  this.setData({confirming:true,error:''})
  try{
   const dates=this.data.all?(this.selectedDates || (this.data.scopeDates.length?this.data.scopeDates:null)):[this.data.date]
   let p=await api.previewPayment(this.data.tripId,dates)
   if(!p.groups.length){wx.showToast({title:'当前无需转账',icon:'none'});return}
   let wrote=false
   for(const currency of p.groups.map(g=>g.currency)){
    p=await api.previewPayment(this.data.tripId,dates)
    const g=p.groups.find(g=>g.currency===currency);if(!g)continue
    let marks=this.data.transfers
    if(typeof api.getLedger==='function'){const ledger=await api.getLedger(this.data.tripId);marks=(ledger.payer_marks || []).filter(m=>!m.revoked_at && !m.confirmed_payment_id && !m.stale && m.currency===currency && Number(m.amount)===Number(g.amount) && (m.dates || []).length===p.dates.length && p.dates.every(d=>(m.dates || []).includes(d))).map(m=>({...m,marked:true,markId:m.id}))}
    const validMark=marks.find(t=>t.currency===currency && t.marked)
    if(!g.offset_only && g.to_participant_id!==this.data.current && validMark)continue
    const content=g.offset_only?'所选日期互相抵扣，无需实际转账。确认结清？':`${this.data.labels[g.from_participant_id] || '同行人'} 付给 ${this.data.labels[g.to_participant_id] || '同行人'} ${g.amount} ${g.currency}。请核对已实际转账后再标记。`
    const recipient=!!g.offset_only || g.to_participant_id===this.data.current
    this.setData({busy:false})
    if(!await ui.confirm(content,recipient?'确认已收款':'我已付款')){if(wrote)await this.reload();return}
    this.setData({busy:true})
    const mark=validMark
    const payload={dates:p.dates,token:p.token,currency}
    if(recipient){if(mark && mark.markId)payload.mark_id=mark.markId;await api.markPayment(this.data.tripId,payload)}
    else await api.markPaid(this.data.tripId,payload)
    wrote=true
   }
   await this.reload()
  }catch(e){await this.reload();this.setData({error:e.message || '结清失败，请刷新后重试'})}
  finally{this.setData({busy:false,confirming:false})}
 },
 async revoke(e){
  if(this.data.loading || this.data.busy || this.data.confirming)return
  const id=e && e.currentTarget.dataset.id
  const records=id?this.data.payments.filter(p=>p.id===id):this.data.payments.filter(p=>!p.revoked_at)
  if(!records.length)return
  if(!await ui.confirm('撤销后恢复待结清金额；历史转账记录仍会保留。','撤销结清？'))return
  this.setData({busy:true,error:''})
  try{for(const p of records)await api.revokePayment(this.data.tripId,p.id);await this.reload()}
  catch(err){await this.reload();this.setData({error:err.message || '撤销失败'})}finally{this.setData({busy:false})}
 },
 copyAmount(e){const p=this.data.transfers[Number(e.currentTarget.dataset.index || 0)];if(p)wx.setClipboardData({data:p.amount})},
 copyBill(e){const p=this.data.transfers[Number(e.currentTarget.dataset.index || 0)];if(p)wx.setClipboardData({data:this.data.name+' · '+this.data.title+'：'+p.from+'付给'+p.to+' '+p.amountText+'（'+this.data.expenseCount+' 笔，共 '+this.data.original+'）'})},
 onShareAppMessage(){return {title:this.data.name+' · '+this.data.title+'账单，请核对',path:'/pages/day-bill/index?tripId='+encodeURIComponent(this.data.tripId)+(this.data.date?'&date='+this.data.date:this.selectedDates?'&dates='+encodeURIComponent(this.selectedDates.join(',')):'')+'&name='+encodeURIComponent(this.data.name)}},
 openExpense(e){wx.navigateTo({url:`/pages/expense/index?tripId=${encodeURIComponent(this.data.tripId)}&expenseId=${encodeURIComponent(e.currentTarget.dataset.id)}`})},
})
