const { publishSettlement, createShareLink } = require("../../services/api")

function readJson(value, fallback) {
  try { return value ? JSON.parse(decodeURIComponent(value)) : fallback } catch (_) { return fallback }
}

function publicShareUrl(token) {
  const app = getApp()
  const baseUrl = app.globalData.publicShareBaseUrl || app.globalData.apiBaseUrl
  return `${baseUrl.replace(/\/$/, "")}/public/share/${token}`
}

Page({
  data: { transfers: [], tripId: "", previewPayload: null, published: false, publishing: false, sharing: false, expiryIndex: 1, expiryDays: [1, 7, 30], shareUrl: "", shareExpiresAt: "" },
  onLoad(query) {
    const result = readJson(query.result, {})
    const previewPayload = readJson(query.preview, null)
    this.setData({ tripId: query.tripId || "", previewPayload, transfers: result.transfers || [] })
  },
  onExpiryChange(e) { this.setData({ expiryIndex: Number(e.detail.value) }) },
  publish() {
    const { tripId, previewPayload, publishing } = this.data
    if (publishing) return
    if (!tripId || !previewPayload) return wx.showToast({ title: "缺少结算数据，请返回重新预览", icon: "none" })
    this.setData({ publishing: true })
    publishSettlement(tripId, previewPayload)
      .then((published) => {
        this.setData({ published: true, transfers: published.result.transfers || this.data.transfers })
        wx.showToast({ title: "结算版本已发布", icon: "success" })
      })
      .catch((e) => wx.showToast({ title: e.message || "发布失败", icon: "none" }))
      .finally(() => this.setData({ publishing: false }))
  },
  share() {
    const { tripId, published, expiryDays, expiryIndex, sharing } = this.data
    if (sharing) return
    if (!published) return wx.showToast({ title: "请先发布结算版本", icon: "none" })
    this.setData({ sharing: true })
    createShareLink(tripId, expiryDays[expiryIndex])
      .then((link) => {
        const shareUrl = publicShareUrl(link.token)
        this.setData({ shareUrl, shareExpiresAt: link.expires_at })
        wx.setClipboardData({ data: shareUrl })
        wx.showToast({ title: "链接已复制", icon: "success" })
      })
      .catch((e) => wx.showToast({ title: e.message || "创建链接失败", icon: "none" }))
      .finally(() => this.setData({ sharing: false }))
  },
  copyShareLink() {
    if (!this.data.shareUrl) return
    wx.setClipboardData({ data: this.data.shareUrl, success: () => wx.showToast({ title: "链接已复制", icon: "success" }) })
  }
})