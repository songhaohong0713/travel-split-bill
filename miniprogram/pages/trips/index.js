const api = require('../../services/api')
const ui = require('../../services/ledger-view')
const offline = require('../../services/offline-trips')
const sortNames={date:'出行日期',recent:'最近记账',pending:'待结清优先'}
Page({
  data:{tripCount:0,trips:[],allTrips:[],summaryGroups:[],loading:true,error:'',authFailed:false,hasLoaded:false,greeting:'',sort:'date',sortLabel:'出行日期',hideSettled:false,sortOpen:false,balanceReady:false,visibleCount:0,singleSummary:true,emptyReady:false,name:'',currency:'CNY',currencies:['CNY','JPY','USD','KRW'],startDate:'',endDate:'',creating:false,currencyOpen:false},
  onLoad(){
        const pref=wx.getStorageSync && wx.getStorageSync('trip-list-preferences') || {}
    this.setData({greeting:ui.greeting(),sort:pref.sort || 'date',sortLabel:sortNames[pref.sort] || '出行日期',hideSettled:!!pref.hideSettled})
    this.showCached()
    return this.retryLogin()
  },
  onShow(){this.setData({greeting:ui.greeting()});if(this.data.hasLoaded)return this.loadTrips()},
  setCards(cards,changes={}){this.tripCards=cards;const balanceReady=cards.every(t=>t.state!=='unknown'),trips=ui.sortTrips(cards,balanceReady?this.data.sort:'date',balanceReady?this.data.hideSettled:false);this.setData({...changes,balanceReady,allTrips:cards,trips,visibleCount:trips.length,tripCount:cards.length})},
  showCached(){
    if(this.data.tripCount)return
    const trips=offline.listCachedTrips()
    const previous=typeof offline.readHomeSummary==='function'?offline.readHomeSummary():null
    if(previous)this.setData(previous)
    if(trips.length){const cards=trips.map(t=>ui.tripCard(t,t._summary || {_unverified:true}));this.setCards(cards,{cached:true})}
  },
  async retryLogin(){
    this.setData({loading:true,error:'',authFailed:false,emptyReady:false})
    try {await api.login();this.setData({hasLoaded:true});await this.loadTrips()}
    catch(e){this.showCached();this.setData({loading:false,authFailed:true,error:'暂时无法加载你的旅行'})}
  },
  async loadTrips(){
    const requestId=(this.loadSequence || 0)+1;this.loadSequence=requestId
    try {
      const [trips,summary]=await Promise.all([api.listTrips(),api.ledgerSummary()])
      if(requestId!==this.loadSequence)return
      const summaryUnavailable=!!summary.compatibilityError || summary.trips.some(t=>t.incomplete);const details=Object.fromEntries(summary.trips.map(t=>[t.id,t]))
      const namedTrips=await Promise.all(trips.map(async trip=>{
        if(!api.listTripMembers)return trip
        try{const members=await api.listTripMembers(trip.id);const companion=members.find(m=>!m.is_current);return {...trip,companion_name:companion && companion.nickname || '同行人'}}catch(e){return trip}
      }))
      if(requestId!==this.loadSequence)return
      const cards=namedTrips.map(t=>ui.tripCard(t,{...(details[t.id] || {}),_unverified:!!summary.compatibilityError || !details[t.id]})),balanceReady=cards.every(t=>t.state!=='unknown'),visible=ui.sortTrips(cards,balanceReady?this.data.sort:'date',balanceReady?this.data.hideSettled:false)
      offline.rememberTrips(namedTrips.map(t=>({...t,_summary:{...(details[t.id] || {}),_unverified:!!summary.compatibilityError || !details[t.id]}})))
      this.tripCards=cards;this.setData({balanceReady,summaryUnavailable,compatibilityError:summary.compatibilityError || (summaryUnavailable?'部分账务尚需核对，汇总暂不可用':''),allTrips:cards,trips:visible,visibleCount:visible.length,singleSummary:summary.groups.length<=1,tripCount:cards.length,emptyReady:cards.length===0,summarySource:cards.filter(t=>(t.pending || []).some(p=>p.direction==='receivable')).map(t=>'「'+t.name+'」').join('、'),summaryGroups:ui.summaryGroups(summary.groups.length?summary.groups:[{currency:'CNY',receivable:'0',payable:'0'}]),settledCount:cards.filter(t=>t.state==='settled').length,loading:false,error:'',authFailed:false,cached:false})
      if(typeof offline.cacheHomeSummary==='function')offline.cacheHomeSummary({summaryUnavailable:this.data.summaryUnavailable,compatibilityError:this.data.compatibilityError,summaryGroups:this.data.summaryGroups,singleSummary:this.data.singleSummary,summarySource:this.data.summarySource})
    }catch(e){if(requestId!==this.loadSequence)return;this.showCached();this.setData({loading:false,emptyReady:false,error:'旅行记录暂时无法加载，请重试',authFailed:e.statusCode===401})}
  },
  openSort(){this.setData({sortOpen:true})},closeSort(){this.setData({sortOpen:false})},stop(){},
  async chooseSort(e){const sort=e.currentTarget.dataset.sort;if(sort==='pending'&&!await this.requireBalances())return;this.applyPreferences(sort,this.data.hideSettled);this.closeSort()},
  async requireBalances(){if((this.tripCards || this.data.allTrips).some(t=>t.state==='unknown'))await this.loadTrips();const ready=!(this.tripCards || this.data.allTrips).some(t=>t.state==='unknown');if(!ready&&wx.showToast)wx.showToast({title:'金额仍在同步，请稍后重试',icon:'none'});return ready},
  async toggleSettled(e){if(!await this.requireBalances())return;this.applyPreferences(this.data.sort,e.detail.value)},
  resetSort(){this.applyPreferences('date',false);this.closeSort()},
  applyPreferences(sort,hideSettled){
    wx.setStorageSync('trip-list-preferences',{sort,hideSettled})
    this.setData({sort,sortLabel:sortNames[sort],hideSettled,visibleCount:ui.sortTrips(this.tripCards || this.data.allTrips,sort,hideSettled).length,trips:ui.sortTrips(this.tripCards || this.data.allTrips,sort,hideSettled)})
  },
  openTrip(e){const t=(this.tripCards || this.data.allTrips).find(t=>t.id===e.currentTarget.dataset.id);if(t)wx.navigateTo({url:ui.tripUrl(t)})},
  createTrip(){wx.navigateTo({url:'/pages/trip-create/index'})},
  quickExpense(){const trips=this.tripCards || this.data.allTrips;if(!trips.length)return this.createTrip();ui.newExpense(trips.find(t=>t.ongoing) || ui.sortTrips(trips,'recent')[0])},
  onName(e){this.setData({name:e.detail.value,validName:!!e.detail.value.trim()})},
  onDates(e){this.setData({startDate:e.detail.start,endDate:e.detail.end})},
  openCurrency(){this.setData({currencyOpen:true})},closeCurrency(){this.setData({currencyOpen:false})},
  chooseCurrency(e){this.setData({currency:e.currentTarget.dataset.currency,currencyOpen:false})},
  async createFirstTrip(){return ui.createTrip.call(this)},
})
