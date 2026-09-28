const { createTripInvite, deleteExpense, deleteTrip, listExpenses } = require("../../services/api")

function confirmDelete(content) {
  return new Promise((resolve) => wx.showModal({ title: "确认删除", content, confirmColor: "#C86D45", success: ({ confirm }) => resolve(confirm) }))
}

function defaultExpenseTitle(now = new Date()) {
  return `${now.getMonth() + 1}月${now.getDate()}日消费`
}

function expenseSummary(record, currency) {
  const bill = (record.payload.expenses || [])[0] || {}
  const items = bill.items || []
  const total = items.reduce((sum, item) => sum + Math.round(Number(item.amount && item.amount.amount) * 100 || 0), 0)
  return { id: record.id, revision: record.revision, occurredAt: record.occurred_at, title: record.payload.title || "未命名消费", payer: bill.payer_id || "", itemCount: items.length, total: (total / 100).toFixed(2), currency, isCreator: Boolean(record.is_creator) }
}


Page({
  data: { tripId: "", currency: "CNY", name: "", isOwner: false, expenses: [], loading: true, creating: false, loadError: "", inviting: false, inviteReady: false, invitePath: "" },
  onLoad(query) {
    this.setData({ tripId: query.tripId, currency: query.currency || "CNY", name: query.name || "旅行账本", isOwner: query.isOwner === "1" || query.isOwner === true })
    return this.loadExpenses()
  },
  loadExpenses() {
    return listExpenses(this.data.tripId)
      .then((records) => this.setData({ expenses: records.map((record) => expenseSummary(record, this.data.currency)), loading: false, loadError: "" }))
      .catch(() => this.setData({ loading: false, loadError: "暂时无法读取消费记录" }))
  },
  openExpense(event) {
    const { id } = event.currentTarget.dataset
    wx.navigateTo({ url: `/pages/expense/index?tripId=${encodeURIComponent(this.data.tripId)}&currency=${encodeURIComponent(this.data.currency)}&expenseId=${encodeURIComponent(id)}` })
  },
  chooseReceiptAndCreate() {
    if (this.data.creating) return Promise.resolve()
    return new Promise((resolve) => wx.chooseMedia({ count: 1, mediaType: ["image"], sourceType: ["camera", "album"], success: ({ tempFiles }) => {
      const receiptPath = tempFiles[0].tempFilePath
      wx.compressImage({ src: receiptPath, quality: 80, success: ({ tempFilePath }) => { getApp().globalData.pendingReceiptPath = tempFilePath; resolve(this.createExpenseRecord()) }, fail: () => { getApp().globalData.pendingReceiptPath = receiptPath; resolve(this.createExpenseRecord()) } })
    }, fail: () => resolve() }))
  },
  createExpenseRecord() {
    if (this.data.creating) return Promise.resolve()
    this.setData({ creating: true })
    wx.navigateTo({ url: `/pages/expense/index?tripId=${encodeURIComponent(this.data.tripId)}&currency=${encodeURIComponent(this.data.currency)}` })
    this.setData({ creating: false })
    return Promise.resolve()
  },
  prepareInvite() {
    if (this.data.inviting) return Promise.resolve()
    this.setData({ inviting: true, inviteReady: false, invitePath: "" })
    return createTripInvite(this.data.tripId)
      .then((invite) => this.setData({ inviteReady: true, invitePath: `/pages/trip-invite/index?token=${encodeURIComponent(invite.token)}` }))
      .catch((error) => wx.showToast({ title: error.message || "创建邀请失败", icon: "none" }))
      .finally(() => this.setData({ inviting: false }))
  },
  deleteExpenseRecord(event) {
    const { id } = event.currentTarget.dataset
    const expense = this.data.expenses.find((item) => item.id === id)
    if (!this.data.isOwner && !(expense && expense.isCreator)) return Promise.resolve()
    return confirmDelete("删除后无法恢复这笔消费记录。")
      .then((confirmed) => confirmed ? deleteExpense(this.data.tripId, id) : null)
      .then((result) => result === null ? null : this.loadExpenses())
      .catch((error) => wx.showToast({ title: error.message || "删除失败", icon: "none" }))
  },
  deleteTripRecord() {
    if (!this.data.isOwner) return Promise.resolve()
    return confirmDelete("将永久删除全部消费、邀请和结算记录。")
      .then((confirmed) => confirmed ? deleteTrip(this.data.tripId) : null)
      .then((result) => { if (result !== null) wx.navigateBack() })
      .catch((error) => wx.showToast({ title: error.message || "删除失败", icon: "none" }))
  },
  onShareAppMessage() {
    return { title: "邀请你一起记旅行账", path: this.data.invitePath }
  },
})

if (typeof module !== "undefined") module.exports = { defaultExpenseTitle, expenseSummary }
