const { login, createTrip, listTrips } = require("../../services/api")

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
      .then((trips) => this.setData({ trips, loadingTrips: false, loadError: "" }))
      .catch(() => this.setData({ loadingTrips: false, loadError: "暂时无法读取旅行记录" }))
  },
  onName(e) { this.setData({ name: e.detail.value }) },
  onCurrency(e) { this.setData({ currency: this.data.currencies[e.detail.value] }) },
  toggleCreateForm() { this.setData({ showCreateForm: !this.data.showCreateForm }) },
  openTrip(e) {
    const { id, currency, owner } = e.currentTarget.dataset
    wx.navigateTo({ url: `/pages/trip-detail/index?tripId=${encodeURIComponent(id)}&currency=${encodeURIComponent(currency)}&name=${encodeURIComponent(e.currentTarget.dataset.name || "旅行账本")}&isOwner=${owner ? "1" : "0"}` })
  },
  createTrip() {
    if (!this.data.name.trim()) return wx.showToast({ title: "请输入旅行名称", icon: "none" })
    this.setData({ creating: true })
    return createTrip(this.data.name.trim(), this.data.currency)
      .then((trip) => wx.navigateTo({ url: `/pages/trip-detail/index?tripId=${trip.id}&currency=${trip.default_currency}&name=${encodeURIComponent(trip.name)}&isOwner=1` }))
      .catch((error) => wx.showToast({ title: error.message || "创建失败", icon: "none" }))
      .finally(() => this.setData({ creating: false }))
  },
})
