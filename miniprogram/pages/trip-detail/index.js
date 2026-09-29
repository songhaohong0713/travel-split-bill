const { createTripInvite, deleteExpense, deleteTrip, listExpenses, previewSettlement } = require("../../services/api")

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
  const itemCurrency = items[0] && items[0].amount && items[0].amount.currency || currency
  return { id: record.id, revision: record.revision, occurredAt: record.occurred_at, title: record.payload.title || "未命名消费", payer: bill.payer_id || "", itemCount: items.length, total: (total / 100).toFixed(2), currency: itemCurrency, isCreator: Boolean(record.is_creator) }
}

function buildOverview(records, currency) {
  const summaries = records.map((record) => expenseSummary(record, currency))
  const included = summaries.filter((record) => record.currency === currency)
  const cents = included.reduce((total, record) => total + Math.round(Number(record.total) * 100 || 0), 0)
  return { recordCount: records.length, total: (cents / 100).toFixed(2), currency, mixedCurrency: included.length !== summaries.length }
}

function validAmount(value) {
  return /^\d+(\.\d{1,2})?$/.test(String(value || "").trim()) && Number(value) > 0
}

function buildSettlementPreview(records, currency) {
  const participants = [...new Set(records.flatMap((record) => record.payload && record.payload.participants || []).map((name) => String(name || "").trim()).filter(Boolean))]
  const expenses = records.flatMap((record) => record.payload && record.payload.expenses || [])
    .filter((expense) => Array.isArray(expense.items) && expense.items.length)
    .map((expense) => ({
      ...expense,
      actual_payment: expense.actual_payment && validAmount(expense.actual_payment.amount) ? expense.actual_payment : undefined,
    }))
  return participants.length && expenses.length ? { settlement_currency: currency, participants, expenses } : null
}

function settlementGroups(result, participants) {
  const groups = result && Array.isArray(result.groups) ? result.groups : result ? [{ currency: ((result.transfers || [])[0] || {}).amount && result.transfers[0].amount.currency || "CNY", ...result }] : []
  return groups.map((group) => ({
    currency: group.currency,
    participants: participants.map((name) => ({
      name,
      paid: group.paid_by_participant && group.paid_by_participant[name] ? group.paid_by_participant[name].amount : "0.00",
      responsibility: group.responsibility_by_participant && group.responsibility_by_participant[name] ? group.responsibility_by_participant[name].amount : "0.00",
    })),
    transferText: (group.transfers || []).length
      ? group.transfers.map((transfer) => `${transfer.from_participant_id} 需付给 ${transfer.to_participant_id} ¥${transfer.amount.amount} ${transfer.amount.currency}`).join("；")
      : `双方已结清 ${group.currency}`,
  }))
}

Page({
  data: { tripId: "", currency: "CNY", name: "", isOwner: false, expenses: [], overview: { recordCount: 0, total: "0.00", currency: "CNY", mixedCurrency: false }, settlementGroups: [], settlementWarning: "", hasLoaded: false, loading: true, creating: false, loadError: "", inviting: false, inviteReady: false, invitePath: "" },
  onLoad(query) {
    this.setData({ tripId: query.tripId, currency: query.currency || "CNY", name: query.name || "旅行账本", isOwner: query.isOwner === "1" || query.isOwner === true })
    return this.loadExpenses().then(() => this.setData({ hasLoaded: true }))
  },
  onShow() { return this.data.hasLoaded ? this.loadExpenses() : Promise.resolve() },
  loadExpenses() {
    return listExpenses(this.data.tripId)
      .then((records) => {
        this.setData({ expenses: records.map((record) => expenseSummary(record, this.data.currency)), overview: buildOverview(records, this.data.currency), settlementGroups: [], settlementWarning: "", loading: false, loadError: "" })
        const preview = buildSettlementPreview(records, this.data.currency)
        if (!preview || typeof previewSettlement !== "function") {
          if (records.length) this.setData({ settlementWarning: "补全同行人、分摊或汇率后可查看结算" })
          return records
        }
        return previewSettlement(this.data.tripId, preview)
          .then((result) => { this.setData({ settlementGroups: settlementGroups(result, preview.participants), settlementWarning: "" }); return records })
          .catch(() => { this.setData({ settlementGroups: [], settlementWarning: "补全同行人、分摊或汇率后可查看结算" }); return records })
      })
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

if (typeof module !== "undefined") module.exports = { buildOverview, buildSettlementPreview, defaultExpenseTitle, expenseSummary, settlementGroups }
