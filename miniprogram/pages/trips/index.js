const api = require('../../services/api')
const ui = require('../../services/ledger-view')
Page({
  data:{tripCount:0,hasSummary:false,trips:[],summaryGroups:[],loading:true,error:'',authFailed:false,hasLoaded:false,pendingTrips:0,greeting:''},
  onLoad(){this.setData({greeting:new Date().getHours()<12?'上午好':new Date().getHours()<18?'下午好':'晚上好'});return this.retryLogin()},
  onShow(){if(this.data.hasLoaded)return this.loadTrips()},
  async retryLogin(){
    this.setData({loading:true,error:'',authFailed:false})
    try {await api.login();this.setData({hasLoaded:true});await this.loadTrips()}
    catch(e){this.setData({loading:false,authFailed:true})}
  },
  async loadTrips(){
    try {
      const [trips,summary]=await Promise.all([api.listTrips(),api.ledgerSummary()])
      const details=Object.fromEntries(summary.trips.map(t=>[t.id,t]))
      const cards=trips.slice().reverse().map(t=>ui.tripCard(t,details[t.id]))
      this.setData({hasTrips:cards.length>0,tripCount:cards.length,hasSummary:summary.groups.length>0,trips:cards,summaryGroups:summary.groups.map(g=>({...g,symbol:ui.symbol(g.currency),amount:(Number(g.receivable)-Number(g.payable)).toFixed(['JPY','KRW'].includes(g.currency)?0:2),direction:Number(g.receivable)>=Number(g.payable)?'同行人需付给你':'你需付给同行人'})),pendingTrips:cards.filter(t=>t.hasPending).length,loading:false,error:'',authFailed:false})
    }catch(e){this.setData({loading:false,error:'旅行记录暂时无法加载，请重试',authFailed:e.statusCode===401})}
  },
  openTrip(e){const t=this.data.trips.find(t=>t.id===e.currentTarget.dataset.id);if(t)wx.navigateTo({url:ui.tripUrl(t)})},
  createTrip(){wx.navigateTo({url:'/pages/trip-create/index'})},
  quickExpense(){
    const trips=this.data.trips
    if(!trips.length)return this.createTrip()
    const ongoing=trips.find(t=>t.ongoing)
    if(ongoing || trips.length===1)return ui.newExpense(ongoing || trips[0])
    wx.navigateTo({url:'/pages/expense/index'})
  },
})
