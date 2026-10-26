const api=require('../../services/api')
const ui=require('../../services/ledger-view')
const {memberLabelMap}=require('../../services/member-labels')
const {expenseSummary}=require('../../services/expense-summary')
function timeLabel(value){if(!value)return '';const d=new Date(value);const day=`${d.getMonth()+1}/${d.getDate()}`;const today=new Date();return `${d.toDateString()===today.toDateString()?'今天':day} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`}
Page({
 data:{expenseCount:0,transferCount:0,hasGroups:false,tripId:'',date:'',all:false,name:'',loading:true,busy:false,error:'',day:null,expenses:[],payments:[],pending:[],groupRows:[],previewGroups:[],scopeDates:[],scopeMessage:'',transfers:[],carry:[]},
 onLoad(q){this.setData({tripId:q.tripId,date:q.date || '',all:!q.date,name:decodeURIComponent(q.name || '')});return this.reload()},
 onShow(){if(this.loaded)return this.reload()},
 async reload(){
  this.setData({loading:true,error:'',previewGroups:[],scopeDates:[],transfers:[],pending:[],scopeMessage:''})
  try{
   const [ledger,records,members]=await Promise.all([api.getLedger(this.data.tripId),api.listExpenses(this.data.tripId),api.listTripMembers(this.data.tripId)])
   const labels=memberLabelMap(members)
   const day=ledger.days.find(d=>d.date===this.data.date) || null
   const closed=ledger.days.filter(d=>d.status!=='recording')
   const filtered=records.filter(r=>this.data.all?closed.some(d=>d.date===r.occurred_at):r.occurred_at===this.data.date)
   const expenses=filtered.map(r=>({...expenseSummary(r,'CNY',labels),date:r.occurred_at}))
   const pending=(day && day.pending || []).map(p=>({...p,text:`${labels[p.from_participant_id] || '同行人'} 付给 ${labels[p.to_participant_id] || '同行人'}`}))
   const groups=day && day.groups || []
   const groupRows=groups.map(g=>({currency:g.currency,members:members.map(m=>({id:m.id,name:labels[m.id],paid:(g.paid_by_participant[m.id] || {}).amount || '0.00',responsibility:(g.responsibility_by_participant[m.id] || {}).amount || '0.00'}))}))
   const payments=ledger.payments.filter(p=>this.data.all || p.allocations.some(a=>a.date===this.data.date)).map(p=>({...p,by:labels[p.created_by] || '同行人',time:timeLabel(p.created_at),direction:`${labels[p.from_participant_id] || '同行人'} → ${labels[p.to_participant_id] || '同行人'}`})).reverse()
   const active=payments.filter(p=>!p.revoked_at)
   const transferView=p=>({...p,symbol:ui.symbol(p.currency),from:labels[p.from_participant_id] || '同行人',to:labels[p.to_participant_id] || '同行人'})
   const transfers=(day && day.status==='settled'?active.map(p=>({...ui.dayPayment(p,this.data.date,ledger.members || members.map(m=>m.id)),settled:true})):pending).map(transferView)
   this.setData({day,labels,expenses,expenseCount:expenses.length,payments,hasActivePayments:active.length>0,groupRows,hasGroups:groupRows.length>0,pending,transfers,transferCount:transfers.length,hasCarry:!!(day && day.carry_entries && day.carry_entries.length),carry:day && day.carry_entries || [],title:this.data.all?'合并结清':this.data.date?ui.dateTitle(this.data.date):'当天账单',original:ui.originalTotals(filtered),statusLabel:day?{recording:'记录中',pending:'待转账',settled:'已结清',invalid:'需补全'}[day.status]:'',loading:false,historyPending:ledger.history_status==='pending'})
   this.loaded=true
   if(this.data.all){
    if(!closed.length){this.setData({scopeMessage:'还没有待结清日期'});return}
    const p=await api.previewPayment(this.data.tripId)
    this.setData({previewGroups:p.groups,scopeDates:p.dates,transferCount:p.groups.length,transfers:p.groups.map(transferView),scopeMessage:p.groups.length?'':'所选日期已结清'})
   }
  }catch(e){this.setData({loading:false,error:e.message || '账单暂时无法加载，请重试',transfers:[],previewGroups:[],scopeDates:[]})}
 },
 async endDay(){
  if(this.data.busy)return
  if(!await ui.confirm('结束后计入旅行待结清，仍可补记消费。','结束当天记账'))return
  this.setData({busy:true,error:''})
  try{await api.closeDay(this.data.tripId,this.data.date);await this.reload()}
  catch(e){this.setData({error:e.message || '操作失败'})}finally{this.setData({busy:false})}
 },
 async confirmPayment(){
  if(this.data.busy)return
  this.setData({busy:true,error:''})
  try{
   const dates=this.data.all?null:[this.data.date]
   let p=await api.previewPayment(this.data.tripId,dates)
   if(!p.groups.length){wx.showToast({title:'当前无需转账',icon:'none'});return}
   for(const currency of p.groups.map(g=>g.currency)){
    p=await api.previewPayment(this.data.tripId,dates)
    const g=p.groups.find(g=>g.currency===currency);if(!g)continue
    const content=g.offset_only?'所选日期互相抵扣，无需实际转账。确认结清？':`${this.data.labels[g.from_participant_id] || '同行人'} 付给 ${this.data.labels[g.to_participant_id] || '同行人'} ${g.amount} ${g.currency}。请核对已实际转账后再标记。`
    if(!await ui.confirm(content,'标记为已转账'))break
    await api.markPayment(this.data.tripId,{dates:p.dates,token:p.token,currency})
   }
   await this.reload()
  }catch(e){await this.reload();this.setData({error:e.message || '结清失败，请刷新后重试'})}
  finally{this.setData({busy:false})}
 },
 async revoke(e){
  if(this.data.busy)return
  const id=e && e.currentTarget.dataset.id
  const records=id?this.data.payments.filter(p=>p.id===id):this.data.payments.filter(p=>!p.revoked_at)
  if(!records.length)return
  if(!await ui.confirm('撤销后恢复待结清金额；历史转账记录仍会保留。','撤销已转账'))return
  this.setData({busy:true,error:''})
  try{for(const p of records)await api.revokePayment(this.data.tripId,p.id);await this.reload()}
  catch(err){await this.reload();this.setData({error:err.message || '撤销失败'})}finally{this.setData({busy:false})}
 },
 copyAmount(e){const p=this.data.transfers[Number(e.currentTarget.dataset.index || 0)];if(p)wx.setClipboardData({data:p.amount})},
 openWechat(){wx.showModal({title:'前往微信转账',content:'金额可先复制。请点击右上角关闭小程序，返回微信聊天完成转账，再回来标记。',showCancel:false})},
 openExpense(e){wx.navigateTo({url:`/pages/expense/index?tripId=${encodeURIComponent(this.data.tripId)}&expenseId=${encodeURIComponent(e.currentTarget.dataset.id)}`})},
})
