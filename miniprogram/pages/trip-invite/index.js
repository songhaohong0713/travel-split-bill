const { ensureAuthenticated, getTripInvite, acceptTripInvite } = require("../../services/api")

Page({
  data: { token: "", invite: null, loading: true, joining: false, error: "" },
  onLoad(query) {
    const token = query.token || ""
    if (!token) return this.setData({ loading: false, error: "邀请链接无效" })
    this.setData({ token })
    return ensureAuthenticated().then(() => getTripInvite(token)).then((invite) => this.setData({ invite, loading: false })).catch((error) => this.setData({ loading: false, error: error.message || "登录或加载邀请失败，请重试" }))
  },
  joinTrip() {
    if (!this.data.token || this.data.joining) return
    this.setData({ joining: true })
    ensureAuthenticated().then(() => acceptTripInvite(this.data.token)).then(() => {
      wx.showToast({ title: "已加入旅行", icon: "success" })
      wx.reLaunch({ url: "/pages/trips/index" })
    }).catch((error) => this.setData({ error: error.message || "加入失败" })).finally(() => this.setData({ joining: false }))
  },
  onShareAppMessage() { return { title: "邀请你一起记旅行账", path: `/pages/trip-invite/index?token=${this.data.token}` } },
})
