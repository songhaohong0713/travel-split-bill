const { createExpense, createTripInvite, listExpenses } = require("../../services/api")

function defaultExpenseTitle(now = new Date()) {
  return `${now.getMonth() + 1}月${now.getDate()}日消费`
}

function expenseSummary(record, currency) {
  const bill = (record.payload.expenses || [])[0] || {}
  const items = bill.items || []
  const total = items.reduce((sum, item) => sum + Math.round(Number(item.amount && item.amount.amount) * 100 || 0), 0)
  return { id: record.id, revision: record.revision, occurredAt: record.occurred_at, title: record.payload.title || "未命名消费", payer: bill.payer_id || "", itemCount: items.length, total: (total / 100).toFixed(2), currency }
}

function emptyPayload(title, currency) {
  return { title, participants: ["我", ""], settlement_currency: currency, expenses: [{ expense_id: `bill-${Date.now()}`, payer_id: "我", items: [], adjustments: [] }] }
}

Page({
  data: { tripId: "", currency: "CNY", name: "", expenses: [], loading: true, creating: false, loadError: "", inviting: false, inviteReady: false, invitePath: "" },
  onLoad(query) {
    this.setData({ tripId: query.tripId, currency: query.currency || "CNY", name: query.name || "旅行账本" })
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
    return new Promise((resolve) => wx.chooseMedia({ count: 1, mediaType: ["image"], sourceType: ["camera", "album"], success: ({ tempFiles }) => resolve(this.createExpenseRecord(tempFiles[0].tempFilePath)), fail: () => resolve() }))
  },
  createExpenseRecord(receiptPath = "") {
    if (this.data.creating) return Promise.resolve()
    const occurredAt = new Date().toISOString().slice(0, 10)
    this.setData({ creating: true })
    return createExpense(this.data.tripId, occurredAt, emptyPayload(defaultExpenseTitle(), this.data.currency))
      .then((record) => wx.navigateTo({ url: `/pages/expense/index?tripId=${encodeURIComponent(this.data.tripId)}&currency=${encodeURIComponent(this.data.currency)}&expenseId=${encodeURIComponent(record.id)}${receiptPath ? `&receiptPath=${encodeURIComponent(receiptPath)}` : ""}` }))
      .catch((error) => wx.showToast({ title: error.message || "新建消费失败", icon: "none" }))
      .finally(() => this.setData({ creating: false }))
  },
  prepareInvite() {
    if (this.data.inviting) return Promise.resolve()
    this.setData({ inviting: true, inviteReady: false, invitePath: "" })
    return createTripInvite(this.data.tripId)
      .then((invite) => this.setData({ inviteReady: true, invitePath: `/pages/trip-invite/index?token=${encodeURIComponent(invite.token)}` }))
      .catch((error) => wx.showToast({ title: error.message || "创建邀请失败", icon: "none" }))
      .finally(() => this.setData({ inviting: false }))
  },
  onShareAppMessage() {
    return { title: "邀请你一起记旅行账", path: this.data.invitePath }
  },
})

if (typeof module !== "undefined") module.exports = { defaultExpenseTitle, expenseSummary, emptyPayload }
