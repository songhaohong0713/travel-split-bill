const api=require('../../services/api')
const ui=require('../../services/ledger-view')
const {memberLabelMap}=require('../../services/member-labels')
Page({
  data:{dayCount:0,hasSummary:false,tripId:'',name:'',currency:'CNY',subtitle:'',days:[],summary:[],pendingDays:0,isOwner:false,memberCount:1,loading:true,error:'',inviteOpen:false,invitePath:'',inviting:false,inviteError:'',dateOpen:false,startDate:'',endDate:''},
  async onLoad(q){
    this.setData({tripId:q.tripId,name:decodeURIComponent(q.name || ''),currency:q.currency || 'CNY'})
    await this.reload()
    if(q.dateFailed)wx.showToast({title:'旅行已创建，日期未保存，可在菜单补填',icon:'none'})
    if(q.inviteGuide==='1' && this.data.isOwner && this.data.memberCount<2)await this.prepareInvite()
  },
  onShow(){if(this.loaded)return this.reload()},
  async reload(){
    this.setData({loading:true,error:''})
    try{
      const [trips,ledger,members,records]=await Promise.all([api.listTrips(),api.getLedger(this.data.tripId),api.listTripMembers(this.data.tripId),api.listExpenses(this.data.tripId)])
      const trip=trips.find(t=>t.id===this.data.tripId)
      if(!trip)throw {message:'旅行不存在或你尚未加入'}
      const labels=memberLabelMap(members)
      const current=(members.find(m=>m.is_current) || {}).id
      const describe=p=>p.to_participant_id===current ? '同行人付我' : '我付同行人'
      const summary=(ledger.summary || []).filter(p=>!p.offset_only).map(p=>({...p,symbol:ui.symbol(p.currency),direction:p.to_participant_id===current?'同行人需付给你':'你需付给同行人'}))
      const days=ledger.days.map(day=>{
        const list=records.filter(r=>r.occurred_at===day.date)
        const pending=(day.pending || []).filter(p=>Number(p.amount)>0)
        const paid=ledger.payments.filter(p=>!p.revoked_at && p.allocations.some(a=>a.date===day.date))
        return {...day,title:ui.dateTitle(day.date),original:ui.originalTotals(list),statusLabel:{recording:'记录中',pending:'待转账',settled:'已结清',invalid:'需补全'}[day.status],balanceText:pending.length?pending.map(p=>`${describe(p)} ${ui.symbol(p.currency)}${p.amount}`).join(' / '):day.status==='settled' && paid.length?'已转 '+paid.map(p=>`${ui.symbol(p.currency)}${ui.dayPayment(p,day.date,ledger.members).amount}`).join(' / '):day.status==='settled'?'无需转账':'',hasCarry:Boolean(day.carry_entries && day.carry_entries.length)}
      })
      const card=ui.tripCard(trip,{day_count:days.length})
      this.setData({name:trip.name,currency:trip.default_currency,isOwner:trip.is_owner,memberCount:members.length,subtitle:card.subtitle,startDate:trip.start_date || '',endDate:trip.end_date || '',days,dayCount:days.length,summary,hasSummary:summary.length>0,pendingDays:days.filter(d=>d.status==='pending').length,loading:false,summaryIncomplete:ledger.summary_incomplete})
      this.loaded=true
    }catch(e){this.setData({loading:false,error:e.message || '账单暂时无法加载，请重试',summary:[],days:[]})}
  },
  addExpense(){ui.newExpense({id:this.data.tripId,default_currency:this.data.currency})},
  openDay(e){wx.navigateTo({url:`/pages/day-bill/index?tripId=${encodeURIComponent(this.data.tripId)}&date=${e.currentTarget.dataset.date}&name=${encodeURIComponent(this.data.name)}`})},
  settleTrip(){wx.navigateTo({url:`/pages/day-bill/index?tripId=${encodeURIComponent(this.data.tripId)}&name=${encodeURIComponent(this.data.name)}`})},
  async prepareInvite(){
    if(!this.data.isOwner || this.data.memberCount>=2 || this.data.inviting)return
    this.setData({inviteOpen:true,inviting:true,inviteError:'',invitePath:''})
    try{const invite=await api.createTripInvite(this.data.tripId);this.setData({invitePath:`/pages/trip-invite/index?token=${encodeURIComponent(invite.token)}`})}
    catch(e){this.setData({inviteError:e.message || '邀请创建失败，请重试'})}
    finally{this.setData({inviting:false})}
  },
  closeInvite(){this.setData({inviteOpen:false})},
  onShareAppMessage(){return {title:`邀请你加入「${this.data.name}」一起记账`,path:this.data.invitePath || ui.tripUrl({id:this.data.tripId,name:this.data.name,default_currency:this.data.currency})}},
  menu(){
    if(!this.data.isOwner)return
    wx.showActionSheet({itemList:['修改出行日期','删除旅行'],success:async({tapIndex})=>{
      if(tapIndex===0){this.setData({dateOpen:true});return}
      if(await ui.confirm('将永久删除这段旅行及全部消费、邀请和结算记录。','删除旅行')){
        try{await api.deleteTrip(this.data.tripId);wx.navigateBack()}
        catch(e){wx.showToast({title:e.message || '删除失败',icon:'none'})}
      }
    }})
  },
  onDates(e){this.setData({startDate:e.detail.start,endDate:e.detail.end})},
  async saveDates(){try{await api.updateTripDates(this.data.tripId,{start_date:this.data.startDate || null,end_date:this.data.endDate || null});this.setData({dateOpen:false});await this.reload()}catch(e){this.setData({error:e.message || '日期保存失败'})}},
  closeDates(){this.setData({dateOpen:false})},stop(){},
})
