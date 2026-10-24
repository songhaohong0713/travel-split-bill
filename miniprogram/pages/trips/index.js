const { login, createTrip, listTrips } = require("../../services/api")
const { ledgerSummary, updateTripDates } = require('../../services/api')

Page({
  data: {
    name: "",
    currencies: ["CNY", "JPY", "USD", "KRW"],
    currency: "CNY",
    trips: [],
    hasLoaded: false,
    loadingTrips: true,
    loadError: "",
    showCreateForm: false,
    creating: false,
    startDate: '', endDate: '', summaryGroups: [], summaryError: '',
  },
  onLoad() {
    return login()
      .then(() => this.loadTrips())
      .then(() => this.setData({ hasLoaded: true }))
      .catch(() => this.setData({ loadingTrips: false, loadError: "登录未完成，仍可新建旅行" }))
  },
  onShow() {
    if (this.data.hasLoaded) return this.loadTrips()
    return Promise.resolve()
  },
  loadTrips() {
    return listTrips()
      .then((trips) => {
        this.setData({ trips: trips.slice().reverse(), loadingTrips: false, loadError: '' })
        if (typeof ledgerSummary !== 'function') return
        return ledgerSummary().then(summary => {
          const details=Object.fromEntries(summary.trips.map(t=>[t.id,t]))
          this.setData({summaryGroups:summary.groups, trips:this.data.trips.map(trip=>{
            const detail=details[trip.id]
            return {...trip,pendingText:detail ? detail.history_status === 'pending' ? '历史结算待确认' : detail.incomplete ? '需补全消费' : detail.pending.length ? detail.pending.map(g=>g.offset_only ? '可抵扣结清' : `${g.direction === 'receivable' ? '待收' : '待付'} ${g.amount} ${g.currency}`).join('；') : '暂无待结清' : '继续录入'}
          }),summaryError:summary.trips.some(t=>t.incomplete) ? '部分消费待补全，汇总尚不完整' : ''})
        }).catch(error => this.setData({summaryError:error.message || '暂时无法读取待结清金额'}))
      })
      .catch(() => this.setData({ loadingTrips: false, loadError: "暂时无法读取旅行记录" }))
  },
  onName(e) { this.setData({ name: e.detail.value }) },
  onTravelDates(e) { this.setData({startDate:e.detail.start,endDate:e.detail.end}) },
  onStartDate(e) { this.setData({startDate:e.detail.value}) },
  onEndDate(e) { this.setData({endDate:e.detail.value}) },
  onCurrency(e) { this.setData({ currency: this.data.currencies[e.detail.value] }) },
  toggleCreateForm() { this.setData({ showCreateForm: !this.data.showCreateForm }) },
  quickExpense() {
    const trips=this.data.trips
    if (!trips.length) { this.setData({showCreateForm:true}); return }
    wx.showActionSheet({itemList:trips.slice(0,6).map(t=>t.name),success:({tapIndex})=>{
      const trip=trips[tapIndex]
      wx.navigateTo({url:`/pages/expense/index?tripId=${trip.id}&currency=${trip.default_currency}`})
    }})
  },
  openTrip(e) {
    const { id, currency, owner } = e.currentTarget.dataset
    const trip = this.data.trips.find((item) => item.id === id)
    const name = trip && trip.name || "旅行账本"
    wx.navigateTo({ url: `/pages/trip-detail/index?tripId=${encodeURIComponent(id)}&currency=${encodeURIComponent(currency)}&name=${encodeURIComponent(name)}&isOwner=${owner ? "1" : "0"}` })
  },
  createTrip() {
    if (!this.data.name.trim()) return wx.showToast({ title: "请输入旅行名称", icon: "none" })
    if (this.data.startDate && this.data.endDate && this.data.startDate > this.data.endDate) return wx.showToast({title:'结束日期不能早于开始日期',icon:'none'})
    this.setData({ creating: true })
    return createTrip(this.data.name.trim(), this.data.currency)
      .then(async trip => {
        // The trip exists even if its optional dates fail; do not create it again.
        if ((this.data.startDate || this.data.endDate) && typeof updateTripDates === 'function') {
          try { await updateTripDates(trip.id, {start_date:this.data.startDate || null,end_date:this.data.endDate || null}) }
          catch (error) { wx.showToast({title:'旅行已创建，日期保存失败，可稍后补填',icon:'none'}) }
        }
        return wx.navigateTo({url:`/pages/expense/index?tripId=${trip.id}&currency=${trip.default_currency}`})
      })
      .catch((error) => wx.showToast({ title: error.message || "创建失败", icon: "none" }))
      .finally(() => this.setData({ creating: false }))
  },
})
