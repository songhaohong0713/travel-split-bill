const { createExpense, updateExpense, listExpenses, previewSettlement, uploadReceipt, getReceiptJob } = require("../../services/api")

function newItem() {
  return { id: `${Date.now()}-${Math.random().toString(16).slice(2)}`, name: "", amount: "", selected: false, allocationMode: "payer", payerPercent: "100", friendPercent: "0" }
}

function allocationFor(item, payer, friend) {
  if (item.allocationMode === "payer") return { [payer]: "1" }
  if (item.allocationMode === "friend") return { [friend]: "1" }
  if (item.allocationMode === "split") return { [payer]: "0.5", [friend]: "0.5" }
  return { [payer]: String(Number(item.payerPercent) / 100), [friend]: String(Number(item.friendPercent) / 100) }
}

function cents(value) {
  const normalized = String(value || "0").trim()
  if (!/^-?\d+(\.\d{1,2})?$/.test(normalized)) return NaN
  const [whole, fraction = ""] = normalized.split(".")
  return Number(whole) * 100 + Number((fraction + "00").slice(0, 2)) * (whole.startsWith("-") ? -1 : 1)
}

function money(centsValue) {
  return (centsValue / 100).toFixed(2)
}

function selectedItemCount(items) {
  return (items || []).filter((item) => item.selected).length
}

function defaultExpenseTitle(now = new Date()) {
  return `${now.getMonth() + 1}月${now.getDate()}日消费`
}

function allocationMode(item, payer, friend) {
  const allocation = item.allocation || {}
  if (allocation[payer] === "1") return "payer"
  if (allocation[friend] === "1") return "friend"
  return allocation[payer] === "0.5" && allocation[friend] === "0.5" ? "split" : "custom"
}

function hydrateExpense(record) {
  const payload = record.payload || {}
  const bill = (payload.expenses || [])[0] || {}
  const [payer = "我", friend = ""] = payload.participants || []
  return {
    expenseId: record.id,
    revision: record.revision,
    occurredAt: record.occurred_at,
    title: payload.title || "未命名消费",
    payer,
    friend,
    currency: payload.settlement_currency || "CNY",
    expensePayloadId: bill.expense_id || `bill-${record.id}`,
    items: (bill.items || []).map((item) => ({
      id: item.item_id || newItem().id,
      name: item.name || "",
      amount: item.amount && item.amount.amount || "",
      selected: false,
      allocationMode: allocationMode(item, payer, friend),
      payerPercent: String(Math.round(Number((item.allocation || {})[payer] || 0) * 100)),
      friendPercent: String(Math.round(Number((item.allocation || {})[friend] || 0) * 100)),
    })),
  }
}

function buildTripPreview(records, currency) {
  const participants = records[0] && records[0].payload && records[0].payload.participants || []
  if (!participants.length || records.some((record) => JSON.stringify(record.payload.participants || []) !== JSON.stringify(participants))) return null
  return { settlement_currency: currency, participants, expenses: records.flatMap((record) => record.payload.expenses || []) }
}

function billTotal(items, taxAmount, taxIncluded, adjustmentAmount) {
  const itemTotal = (items || []).reduce((total, item) => total + (cents(item.amount) || 0), 0)
  const tax = taxIncluded ? 0 : (cents(taxAmount) || 0)
  return money(itemTotal + tax + (cents(adjustmentAmount) || 0))
}

function validateBill(data) {
  if (!data.payer || !data.payer.trim()) return "请填写付款人"
  if (!data.friend || !data.friend.trim()) return "请填写同行人"
  if (data.payer.trim() === data.friend.trim()) return "两位参与人不能相同"
  if (!data.items || !data.items.length) return "请至少添加一件商品"
  for (const item of data.items) {
    if (!item.name || !item.name.trim()) return "请填写每件商品名称"
    if (!Number.isFinite(cents(item.amount)) || cents(item.amount) <= 0) return "商品金额必须大于 0"
    if (item.allocationMode === "custom" && Number(item.payerPercent) + Number(item.friendPercent) !== 100) return "自定义比例合计必须为 100%"
  }
  if (data.taxAmount && (!Number.isFinite(cents(data.taxAmount)) || cents(data.taxAmount) < 0)) return "税额不能为负数"
  if (data.adjustmentAmount && !Number.isFinite(cents(data.adjustmentAmount))) return "优惠或退款金额格式不正确"
  return ""
}

function buildBillPayload(data) {
  const payer = data.payer.trim()
  const friend = data.friend.trim()
  const items = data.items.map((item, index) => ({
    item_id: item.id,
    amount: { currency: data.currency, amount: money(cents(item.amount)) },
    allocation: allocationFor(item, payer, friend),
    tax_amount: index === 0 && data.taxAmount ? { currency: data.currency, amount: money(cents(data.taxAmount)) } : undefined,
    tax_included: data.taxIncluded,
  }))
  const adjustment = data.adjustmentAmount ? [{
    adjustment_id: "bill-adjustment",
    amount: { currency: data.currency, amount: money(cents(data.adjustmentAmount)) },
    allocation: data.adjustmentType === 1 ? { [payer]: "1" } : allocationFor({ allocationMode: "split" }, payer, friend),
    reason: ["shared_discount_or_refund", "personal_coupon", "later_tax_refund"][data.adjustmentType],
    received_by: data.adjustmentType === 2 ? payer : undefined,
  }] : []
  let paidCents = items.reduce((total, item) => total + cents(item.amount.amount), 0)
  if (data.taxAmount && !data.taxIncluded) paidCents += cents(data.taxAmount)
  if (data.adjustmentAmount) paidCents += cents(data.adjustmentAmount)
  return {
    title: data.title || defaultExpenseTitle(),
    participants: [payer, friend],
    settlement_currency: data.currency,
    expenses: [{
      expense_id: data.expensePayloadId || `bill-${Date.now()}`,
      payer_id: payer,
      items,
      adjustments: adjustment,
      actual_payment: { currency: data.currency, amount: money(paidCents) },
    }],
  }
}

function settlementUrl(tripId, preview, result) {
  return `/pages/settlement/index?tripId=${encodeURIComponent(tripId)}&preview=${encodeURIComponent(JSON.stringify(preview))}&result=${encodeURIComponent(JSON.stringify(result))}`
}

Page({
  data: { tripId: "", expenseId: "", expensePayloadId: "", revision: 0, occurredAt: "", title: "", currency: "CNY", items: [newItem()], payer: "我", friend: "", batchModeIndex: 0, batchModes: ["付款人自己买", "同行人自己买", "两人均分", "自定义比例"], batchPayerPercent: "50", batchFriendPercent: "50", receiptPath: "", ocrCandidates: [], taxAmount: "", taxIncluded: true, adjustmentAmount: "", adjustmentType: 0, adjustmentLabels: ["公共优惠 / 退款", "个人优惠", "后续退税"], ocrStatus: "未上传", saving: false, selectedCount: 0, billTotal: "0.00", sourceText: "", translatedText: "", amount: "", batchEditing: false, showBatchTools: false, showAdjustments: false, expandedItemId: "" },
  syncBill(changes = {}) {
    const next = { ...this.data, ...changes }
    this.setData({ ...changes, selectedCount: selectedItemCount(next.items), showBatchTools: Boolean(next.batchEditing), billTotal: billTotal(next.items, next.taxAmount, next.taxIncluded, next.adjustmentAmount) })
  },
  onLoad(q) {
    const occurredAt = new Date().toISOString().slice(0, 10)
    const receiptPath = q.receiptPath || ""
    this.syncBill({ tripId: q.tripId, currency: q.currency || "CNY", expenseId: q.expenseId || "", occurredAt, title: defaultExpenseTitle(), receiptPath, ocrStatus: receiptPath ? "等待上传识别" : "未上传" })
    const loaded = q.expenseId ? this.loadExpense(q.expenseId) : Promise.resolve()
    return loaded.then(() => { if (receiptPath) this.startOcr(receiptPath) })
  },
  loadExpense(expenseId) {
    return listExpenses(this.data.tripId).then((records) => {
      const record = records.find((item) => item.id === expenseId)
      if (!record) return wx.showToast({ title: "消费记录不存在", icon: "none" })
      this.syncBill(hydrateExpense(record))
    }).catch((error) => wx.showToast({ title: error.message || "读取消费失败", icon: "none" }))
  },
  chooseReceipt() { wx.chooseMedia({ count: 1, mediaType: ["image"], sourceType: ["camera", "album"], success: ({ tempFiles }) => wx.compressImage({ src: tempFiles[0].tempFilePath, quality: 80, success: ({ tempFilePath }) => { this.setData({ receiptPath: tempFilePath, ocrStatus: "等待上传识别" }); this.startOcr(tempFilePath) } }) }) },
  startOcr(filePath) { this.setData({ ocrStatus: "上传并识别中" }); uploadReceipt(this.data.tripId, filePath).then((job) => this.pollOcr(job.id)).catch(() => this.setData({ ocrStatus: "上传或识别失败，可手动录入" })) },
  pollOcr(jobId) { getReceiptJob(jobId).then((job) => { const labels = { queued: "排队识别中", processing: "正在识别", failed: "识别失败，可手动录入" }; if (job.status === "needs_review") return this.applyOcrCandidates(job.candidates || []); this.setData({ ocrStatus: labels[job.status] || job.status, ocrCandidates: [] }); if (job.status === "queued" || job.status === "processing") setTimeout(() => this.pollOcr(jobId), 1500) }).catch(() => this.setData({ ocrStatus: "识别状态查询失败，可手动录入" })) },
  applyOcrCandidates(candidates) {
    const recognized = (candidates || []).map((candidate) => ({ ...newItem(), name: String(candidate.translated_text || candidate.source_text || "").trim(), amount: String(candidate.amount || "").trim(), currency: String(candidate.currency || "").trim().toUpperCase() })).filter((item) => item.name && Number.isFinite(cents(item.amount)) && cents(item.amount) > 0)
    if (!recognized.length) return this.setData({ ocrCandidates: [], ocrStatus: "未识别到有效商品，请手动录入" })
    const starterIsBlank = this.data.items.length === 1 && !String(this.data.items[0].name || "").trim() && !String(this.data.items[0].amount || "").trim()
    const items = starterIsBlank ? recognized : [...this.data.items, ...recognized]
    const currencies = [...new Set(recognized.map((item) => item.currency).filter((currency) => /^[A-Z]{3}$/.test(currency)))]
    const currency = currencies.length === 1 ? currencies[0] : this.data.currency
    const notice = currencies.length > 1 ? "；币种不一致，请手动确认" : ""
    this.syncBill({ items, currency, ocrCandidates: [], ocrStatus: `已自动添加 ${recognized.length} 项，请核对金额${notice}`, expandedItemId: recognized[0].id })
  },
  toggleAdjustments() { this.setData({ showAdjustments: !this.data.showAdjustments }) },
  onTitle(e) { this.setData({ title: e.detail.value }) }, onOccurredAt(e) { this.setData({ occurredAt: e.detail.value }) }, onPayer(e) { this.setData({ payer: e.detail.value }) }, onFriend(e) { this.setData({ friend: e.detail.value }) }, onTaxIncluded(e) { this.syncBill({ taxIncluded: e.detail.value }) }, onTax(e) { this.syncBill({ taxAmount: e.detail.value }) }, onAdjustment(e) { this.syncBill({ adjustmentAmount: e.detail.value }) }, onAdjustmentType(e) { this.setData({ adjustmentType: Number(e.detail.value) }) },
  updateItem(id, field, value) { this.syncBill({ items: this.data.items.map((item) => item.id === id ? { ...item, [field]: value } : item) }) },
  onItemName(e) { this.updateItem(e.currentTarget.dataset.id, "name", e.detail.value) }, onItemAmount(e) { this.updateItem(e.currentTarget.dataset.id, "amount", e.detail.value) },
  onItemMode(e) { const modes = ["payer", "friend", "split", "custom"]; this.updateItem(e.currentTarget.dataset.id, "allocationMode", modes[Number(e.detail.value)]) },
  onItemPayerPercent(e) { this.updateItem(e.currentTarget.dataset.id, "payerPercent", e.detail.value) }, onItemFriendPercent(e) { this.updateItem(e.currentTarget.dataset.id, "friendPercent", e.detail.value) },
  toggleItem(e) { const id = e.currentTarget.dataset.id; this.updateItem(id, "selected", !this.data.items.find((item) => item.id === id).selected) },
  toggleBatchEditing() { const batchEditing = !this.data.batchEditing; this.syncBill({ batchEditing, items: this.data.items.map((item) => ({ ...item, selected: batchEditing ? item.selected : false })) }) },
  toggleItemDetail(e) { const id = e.currentTarget.dataset.id; this.setData({ expandedItemId: this.data.expandedItemId === id ? "" : id }) },
  stopItemEditorTap() {},
  addItem() { this.syncBill({ items: [...this.data.items, newItem()] }) },
  removeItem(e) { if (this.data.items.length === 1) return wx.showToast({ title: "至少保留一件商品", icon: "none" }); this.syncBill({ items: this.data.items.filter((item) => item.id !== e.currentTarget.dataset.id) }) },
  onBatchMode(e) { this.setData({ batchModeIndex: Number(e.detail.value) }) }, onBatchPayerPercent(e) { this.setData({ batchPayerPercent: e.detail.value }) }, onBatchFriendPercent(e) { this.setData({ batchFriendPercent: e.detail.value }) },
  applyBatchAllocation() {
    const selected = this.data.items.filter((item) => item.selected)
    if (!selected.length) return wx.showToast({ title: "请先选择商品", icon: "none" })
    const modes = ["payer", "friend", "split", "custom"]
    const allocationMode = modes[this.data.batchModeIndex]
    if (allocationMode === "custom" && Number(this.data.batchPayerPercent) + Number(this.data.batchFriendPercent) !== 100) return wx.showToast({ title: "自定义比例合计必须为 100%", icon: "none" })
    this.syncBill({ items: this.data.items.map((item) => item.selected ? { ...item, allocationMode, payerPercent: this.data.batchPayerPercent, friendPercent: this.data.batchFriendPercent } : item) })
  },
  saveAndPreview() {
    const error = validateBill(this.data)
    if (error) return Promise.resolve(wx.showToast({ title: error, icon: "none" }))
    const previewPayload = buildBillPayload(this.data)
    this.setData({ saving: true })
    const save = this.data.expenseId
      ? updateExpense(this.data.tripId, this.data.expenseId, this.data.revision, this.data.occurredAt, previewPayload)
      : createExpense(this.data.tripId, this.data.occurredAt, previewPayload)
    return save
      .then((saved) => listExpenses(this.data.tripId).then((records) => records.map((record) => record.id === saved.id ? saved : record)))
      .then((records) => {
        const tripPreview = buildTripPreview(records, this.data.currency)
        if (!tripPreview) throw new Error("请先统一每笔消费的两位同行人")
        return previewSettlement(this.data.tripId, tripPreview).then((result) => ({ result, tripPreview }))
      })
      .then(({ result, tripPreview }) => wx.navigateTo({ url: settlementUrl(this.data.tripId, tripPreview, result) }))
      .catch((error) => wx.showToast({ title: error.message || "保存账单失败", icon: "none" }))
      .finally(() => this.setData({ saving: false }))
  },
})

if (typeof module !== "undefined") module.exports = { allocationFor, billTotal, buildBillPayload, buildTripPreview, defaultExpenseTitle, hydrateExpense, selectedItemCount, validateBill }
