const { createExpense, listExpenses } = require("../../services/api")

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
  data: { tripId: "", currency: "CNY", name: "", expenses: [], loading: true, creating: false, loadError: "" },
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
  createExpenseRecord() {
    if (this.data.creating) return Promise.resolve()
    const occurredAt = new Date().toISOString().slice(0, 10)
    this.setData({ creating: true })
    return createExpense(this.data.tripId, occurredAt, emptyPayload(defaultExpenseTitle(), this.data.currency))
      .then((record) => wx.navigateTo({ url: `/pages/expense/index?tripId=${encodeURIComponent(this.data.tripId)}&currency=${encodeURIComponent(this.data.currency)}&expenseId=${encodeURIComponent(record.id)}` }))
      .catch((error) => wx.showToast({ title: error.message || "新建消费失败", icon: "none" }))
      .finally(() => this.setData({ creating: false }))
  },
})

if (typeof module !== "undefined") module.exports = { defaultExpenseTitle, expenseSummary, emptyPayload }
