const api=require('../../services/api')
const ui=require('../../services/ledger-view')
const money=require('../../services/money')
const offline=require('../../services/offline-trips')
const timezoneOptions=Array.from({length:105},(_,i)=>{const offset=720-i*15,minutes=Math.abs(offset);return {offset,label:'UTC'+(offset<=0?'+':'-')+String(Math.floor(minutes/60)).padStart(2,'0')+':'+String(minutes%60).padStart(2,'0')}})
const {memberLabelMap}=require('../../services/member-labels')
Page({
  data:{dayCount:0,hasSummary:false,tripId:'',name:'',currency:'CNY',subtitle:'',days:[],summary:[],pendingDays:0,isOwner:false,memberCount:1,loading:true,error:'',inviteOpen:false,invitePath:'',inviting:false,inviteError:'',dateOpen:false,startDate:'',endDate:''},
  async onLoad(q){
    this.setData({tripId:q.tripId,name:decodeURIComponent(q.name || ''),currency:q.currency || 'CNY'})
    await this.reload()
    if(q.dateFailed)wx.showToast({title:'旅行已创建，日期未保存，可在菜单补填',icon:'none'})
    if(q.inviteGuide==='1' && !(wx.getStorageSync && wx.getStorageSync('invite-dismissed:'+this.data.tripId)) && this.data.isOwner && this.data.memberCount<2)await this.prepareInvite()
  },
  onShow(){if(this.loaded)return this.reload()},
  async reload(){
    this.setData({loading:true,error:''})
    try{
      const [trips,ledger,members,records]=await Promise.all([api.listTrips().catch(()=>offline.listCachedTrips()),api.getLedger(this.data.tripId),api.listTripMembers(this.data.tripId),api.listExpenses(this.data.tripId)])
      const trip=trips.find(t=>t.id===this.data.tripId)
      if(!trip)throw {message:'旅行不存在或你尚未加入'}
      for(const record of records)record.ledger_summary=(ledger.expense_summaries || []).find(x=>x.id===record.id)
      this.setData({timezoneOffset:trip.timezone_offset_minutes,timezoneNeedsSetup:trip.timezone_offset_minutes==null,timezoneLabels:timezoneOptions.map(t=>t.label),timezoneLabel:(timezoneOptions.find(t=>t.offset===trip.timezone_offset_minutes)||{label:'待创建者补设'}).label,memberRows:members.map(m=>({...m,label:m.is_current?'我':m.nickname || '同行人'})),nickname:(members.find(m=>m.is_current) || {}).nickname || '',draftName:trip.name});const labels=memberLabelMap(members)
      const current=(members.find(m=>m.is_current) || {}).id
      const describe=p=>p.to_participant_id===current ? (labels[p.from_participant_id] || '同行人')+'待付你' : '你待付'+(labels[p.to_participant_id] || '同行人')
      const summary=(ledger.summary || []).filter(p=>!p.offset_only).map(p=>({...p,amountText:money.formatMoney(p.currency,p.amount),direction:describe(p)}))
      offline.cacheLedger(this.data.tripId,ledger)
      const totals=ui.expenseTotals(records,trip.default_currency,labels)
      const days=ledger.days.slice().sort((a,b)=>b.date.localeCompare(a.date)).map(day=>{
        const list=records.filter(r=>r.occurred_at===day.date)
        const pending=(day.pending || []).filter(p=>Number(p.amount)>0)
        const paid=ledger.payments.filter(p=>!p.revoked_at && p.allocations.some(a=>a.date===day.date))
        return {...day,title:ui.dateTitle(day.date),original:ui.originalTotals(list),statusLabel:{recording:'记录中',pending:pending.some(p=>p.to_participant_id===current)?'待收款':'待付款',settled:'已结清',invalid:'需补全'}[day.status],balanceText:pending.length?pending.map(p=>`${describe(p)} ${money.formatMoney(p.currency,p.amount)}`).join(' / '):day.status==='settled' && paid.length?'已转 '+paid.map(p=>money.formatMoney(p.currency,ui.dayPayment(p,day.date,ledger.members).amount)).join(' / '):day.status==='settled'?'无需转账':'',hasCarry:Boolean(day.carry_entries && day.carry_entries.length)}
      })
      const card=ui.tripCard(trip,{day_count:days.length})
      this.setData({name:trip.name,currency:trip.default_currency,isOwner:trip.is_owner,memberCount:members.length,subtitle:[trip.start_date ? trip.start_date.slice(5).replace('-','/')+(trip.end_date?'–'+trip.end_date.slice(5).replace('-','/'):''):'', members.length>1?'我和'+members.filter(m=>!m.is_current).map(m=>labels[m.id]).join('、'):'只有你一人'].filter(Boolean).join(' · '),expenseCount:totals.count,originalTotal:totals.original,settlementTotal:totals.settlement,startDate:trip.start_date || '',endDate:trip.end_date || '',days,dayCount:days.length,summary,hasSummary:summary.length>0 && !ledger.compatibilityError,compatibilityError:ledger.compatibilityError || ledger.summary_error || '',heroState:ledger.compatibilityError?'unavailable':ledger.summary_incomplete || ledger.summary_error?'invalid':!records.length?'empty':days.some(d=>d.status==='recording')?'recording':'settled',pendingDays:days.filter(d=>d.status==='pending').length,loading:false,summaryIncomplete:ledger.summary_incomplete})
      this.loaded=true
    }catch(e){this.setData({loading:false,error:e.message || '账单暂时无法加载，请重试',})}
  },
  addExpense(){ui.newExpense({id:this.data.tripId,default_currency:this.data.currency})},
  openDay(e){wx.navigateTo({url:`/pages/day-bill/index?tripId=${encodeURIComponent(this.data.tripId)}&date=${e.currentTarget.dataset.date}&name=${encodeURIComponent(this.data.name)}`})},
  settleTrip(){wx.navigateTo({url:`/pages/day-bill/index?tripId=${encodeURIComponent(this.data.tripId)}&name=${encodeURIComponent(this.data.name)}&dates=${encodeURIComponent(this.data.days.filter(d=>d.status==='pending').map(d=>d.date).join(','))}`})},
  async prepareInvite(){
    if(!this.data.isOwner || this.data.memberCount>=2 || this.data.inviting)return
    if(offline.isLocalTrip(this.data.tripId)){this.setData({inviteOpen:true,inviteError:'旅行已保存在本机，联网同步后即可邀请同行人。'});return}
    this.setData({inviteOpen:true,inviting:true,inviteError:'',invitePath:''})
    try{const invite=await api.createTripInvite(this.data.tripId);this.setData({invitePath:`/pages/trip-invite/index?token=${encodeURIComponent(invite.token)}`})}
    catch(e){this.setData({inviteError:e.message || '邀请创建失败，请重试'})}
    finally{this.setData({inviting:false})}
  },
  closeInvite(){if(wx.setStorageSync)wx.setStorageSync('invite-dismissed:'+this.data.tripId,true);this.setData({inviteOpen:false})},
  onShareAppMessage(){return {title:`邀请你加入「${this.data.name}」一起记账`,path:this.data.invitePath || ui.tripUrl({id:this.data.tripId,name:this.data.name,default_currency:this.data.currency})}},
  menu(){this.setData({manageOpen:true,draftName:this.data.name})},
  closeManage(){this.setData({manageOpen:false})},
  onDraftName(e){this.setData({draftName:e.detail.value})},
  async setTimezone(e){
    if(!this.data.isOwner||!this.data.timezoneNeedsSetup||this.data.managing)return;
    const selected=timezoneOptions[Number(e.detail.value)];if(!selected)return;
    if(!await ui.confirm('将固定使用 '+selected.label+' 判断每日账单是否结束，双方保持一致，设置后不可更改。','设置旅行时区'))return;
    this.setData({managing:true});try{await api.setTripTimezone(this.data.tripId,selected.offset);await this.reload()}catch(e){this.setData({error:e.message||'时区保存失败'})}finally{this.setData({managing:false})}
  },
  onNickname(e){this.setData({nickname:e.detail.value})},
  async saveManage(){
    if(this.data.managing)return
    this.setData({managing:true,error:''})
    try{
      if(this.data.nickname.trim())await api.updateTripNickname(this.data.tripId,this.data.nickname.trim())
      if(this.data.isOwner){
        if(this.data.draftName.trim()!==this.data.name)await api.updateTripName(this.data.tripId,this.data.draftName.trim())
        await api.updateTripDates(this.data.tripId,{start_date:this.data.startDate || null,end_date:this.data.endDate || null})
      }
      this.setData({manageOpen:false});if(wx.showToast)wx.showToast({title:'已保存设置',icon:'success'});await this.reload()
    }catch(e){this.setData({error:e.message || '设置保存失败'})}finally{this.setData({managing:false})}
  },
  async deleteManagedTrip(){
    if(!this.data.isOwner || this.data.managing)return
    if(!await ui.confirm('将永久删除旅行、全部消费、邀请和结算记录。','删除旅行'))return
    try{await api.deleteTrip(this.data.tripId);wx.reLaunch({url:'/pages/trips/index'})}
    catch(e){this.setData({error:e.message || '删除失败'})}
  },
  onDates(e){this.setData({startDate:e.detail.start,endDate:e.detail.end})},
  async saveDates(){try{await api.updateTripDates(this.data.tripId,{start_date:this.data.startDate || null,end_date:this.data.endDate || null});this.setData({dateOpen:false});await this.reload()}catch(e){this.setData({error:e.message || '日期保存失败'})}},
  closeDates(){this.setData({dateOpen:false})},stop(){},
})
