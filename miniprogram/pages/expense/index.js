const { createExpense, getExchangeRate, updateExpense, listExpenses, listTripMembers, previewSettlement, uploadReceipt, getReceiptJob } = require("../../services/api")

function newItem() {
  return { id: `${Date.now()}-${Math.random().toString(16).slice(2)}`, name: "", amount: "", selected: false, allocationMode: "payer", payerPercent: "100", friendPercent: "0" }
}

function allocationFor(item, payer, friend) {
  if (!friend) return { [payer]: "1" }
  if (item.allocationMode === "payer") return { [payer]: "1" }
  if (item.allocationMode === "friend") return { [friend]: "1" }
  if (item.allocationMode === "split") return { [payer]: "0.5", [friend]: "0.5" }
  return { [payer]: String(Number(item.payerPercent) / 100), [friend]: String(Number(item.friendPercent) / 100) }
}

function memberChoices(members) {
  return (members || []).map((member, index) => ({ id: String(member.id), label: member.is_current ? "我" : (index ? "同行人" : "同行人") }))
}

function memberLabel(options, id) {
  const member = (options || []).find((option) => option.id === id)
  return member ? member.label : id
}

function cents(value) {
  const normalized = String(value || "0").trim()
  if (!/^-?\d+(\.\d{1,2})?$/.test(normalized)) return NaN
  const [whole, fraction = ""] = normalized.split(".")
  return Number(whole) * 100 + Number((fraction + "00").slice(0, 2)) * (whole.startsWith("-") ? -1 : 1)
}

function validPositiveAmount(value) {
  const valueInCents = cents(value)
  return Number.isFinite(valueInCents) && valueInCents > 0
}

function money(centsValue) {
  return (centsValue / 100).toFixed(2)
}

function applyActualPayment(data, amount, currency) {
  const rawActualPaymentAmount = String(amount ?? "").trim()
  const actualPaymentAmount = rawActualPaymentAmount.replace(/^(?:undefined|undefine|null)+/i, "")
  const actualPaymentCurrency = String(currency ?? "").trim().toUpperCase()
  if (!actualPaymentAmount) return { ...data, actualPaymentAmount: "", actualPaymentCurrency: "", settlementCurrencyLocked: false }
  const hasActualPayment = validPositiveAmount(actualPaymentAmount) && /^[A-Z]{3}$/.test(actualPaymentCurrency)
  return { ...data, actualPaymentAmount, actualPaymentCurrency, settlementCurrency: hasActualPayment ? actualPaymentCurrency : data.settlementCurrency, settlementCurrencyLocked: hasActualPayment }
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
  const firstItem = (bill.items || [])[0] || {}
  const adjustment = (bill.adjustments || [])[0] || {}
  const adjustmentTypes = { shared_discount_or_refund: 0, personal_coupon: 1, later_tax_refund: 2 }
  const [payer = "我", friend = ""] = payload.participants || []
  const actualPayment = bill.actual_payment && validPositiveAmount(bill.actual_payment.amount) && /^[A-Z]{3}$/.test(String(bill.actual_payment.currency || ""))
    ? bill.actual_payment
    : null
  return {
    expenseId: record.id,
    revision: record.revision,
    occurredAt: record.occurred_at,
    title: payload.title || "未命名消费",
    payer,
    friend,
    currency: bill.items && bill.items[0] && bill.items[0].amount && bill.items[0].amount.currency || payload.settlement_currency || "CNY",
    settlementCurrency: bill.settlement_currency || payload.settlement_currency || "CNY",
    settlementCurrencyLocked: Boolean(actualPayment),
    actualPaymentAmount: actualPayment && actualPayment.amount || "",
    actualPaymentCurrency: actualPayment && actualPayment.currency || "",
    referenceRate: bill.reference_rate || "",
    referenceRateSource: bill.reference_rate_source || "",
    taxAmount: firstItem.tax_amount && firstItem.tax_amount.amount || "",
    taxIncluded: firstItem.tax_included !== false,
    adjustmentAmount: adjustment.amount && adjustment.amount.amount || "",
    adjustmentType: adjustmentTypes[adjustment.reason] ?? 0,
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
  const participants = [...new Set(records.flatMap((record) => record.payload && record.payload.participants || []).map((participant) => String(participant || "").trim()).filter(Boolean))]
  const expenses = records.flatMap((record) => record.payload && record.payload.expenses || [])
    .filter((expense) => Array.isArray(expense.items) && expense.items.length)
    .map((expense) => ({
      ...expense,
      actual_payment: expense.actual_payment && validPositiveAmount(expense.actual_payment.amount) && /^[A-Z]{3}$/.test(String(expense.actual_payment.currency || ""))
        ? expense.actual_payment
        : undefined,
    }))
  if (!participants.length || !expenses.length) return null
  return { settlement_currency: currency, participants, expenses }
}

function billTotal(items, taxAmount, taxIncluded, adjustmentAmount) {
  const itemTotal = (items || []).reduce((total, item) => total + (cents(item.amount) || 0), 0)
  const tax = taxIncluded ? 0 : (cents(taxAmount) || 0)
  return money(itemTotal + tax + (cents(adjustmentAmount) || 0))
}

function responsibilityRows(data) {
  const people = [data.payer, data.friend].filter(Boolean)
  const totals = Object.fromEntries(people.map((id) => [id, 0]))
  ;(data.items || []).forEach((item) => {
    const amount = cents(item.amount) || 0
    const allocation = allocationFor(item, data.payer, data.friend)
    Object.entries(allocation).forEach(([id, share]) => { totals[id] = (totals[id] || 0) + Math.round(amount * Number(share)) })
  })
  if (!data.taxIncluded && data.taxAmount) {
    const allocation = allocationFor({ allocationMode: "split" }, data.payer, data.friend)
    Object.entries(allocation).forEach(([id, share]) => { totals[id] = (totals[id] || 0) + Math.round((cents(data.taxAmount) || 0) * Number(share)) })
  }
  if (data.adjustmentAmount) {
    const allocation = data.adjustmentType === 1 ? { [data.payer]: "1" } : allocationFor({ allocationMode: "split" }, data.payer, data.friend)
    Object.entries(allocation).forEach(([id, share]) => { totals[id] = (totals[id] || 0) + Math.round((cents(data.adjustmentAmount) || 0) * Number(share)) })
  }
  return people.map((id) => ({ id, label: memberLabel(data.memberOptions, id), amount: money(totals[id] || 0), currency: data.currency }))
}

function expenseAdvanceText(data) {
  const responsibility = (data.responsibilityRows || []).find((row) => row.id === data.payer)
  if (!responsibility) return ""
  const actualIsComparable = validPositiveAmount(data.actualPaymentAmount) && data.actualPaymentCurrency === data.currency
  if (validPositiveAmount(data.actualPaymentAmount) && !actualIsComparable) return "垫付金额将在旅行结算中按结算币种计算"
  const paid = cents(actualIsComparable ? data.actualPaymentAmount : data.billTotal)
  const advance = paid - cents(responsibility.amount)
  if (!Number.isFinite(advance)) return ""
  if (advance > 0) return `${data.payerLabel || responsibility.label}本笔垫付 ¥${money(advance)} ${data.currency}`
  if (advance < 0) return `${data.payerLabel || responsibility.label}本笔应补 ¥${money(-advance)} ${data.currency}`
  return "本笔已结清"
}

function estimatedSettlementAmount(total, rate) {
  const amount = Number(total)
  const referenceRate = Number(rate)
  return Number.isFinite(amount) && amount > 0 && Number.isFinite(referenceRate) && referenceRate > 0
    ? (amount * referenceRate).toFixed(2)
    : ""
}

function checkoutSummary(data) {
  if (validPositiveAmount(data.actualPaymentAmount)) return { label: "实际支付", amount: data.actualPaymentAmount, currency: data.actualPaymentCurrency || data.settlementCurrency }
  if (data.currency !== data.settlementCurrency && data.estimatedSettlementAmount) return { label: "预计结算", amount: data.estimatedSettlementAmount, currency: data.settlementCurrency }
  return { label: "本次应付", amount: data.billTotal, currency: data.currency }
}

function validateBill(data) {
  if (!data.payer || !data.payer.trim()) return "请填写付款人"
  if (data.friend && data.payer.trim() === data.friend.trim()) return "两位参与人不能相同"
  if (!data.items || !data.items.length) return "请至少添加一件商品"
  for (const item of data.items) {
    if (!item.name || !item.name.trim()) return "请填写每件商品名称"
    if (!Number.isFinite(cents(item.amount)) || cents(item.amount) <= 0) return "商品金额必须大于 0"
    if (item.allocationMode === "custom" && Number(item.payerPercent) + Number(item.friendPercent) !== 100) return "自定义比例合计必须为 100%"
  }
  if (data.taxAmount && (!Number.isFinite(cents(data.taxAmount)) || cents(data.taxAmount) < 0)) return "税额不能为负数"
  if (data.adjustmentAmount && !Number.isFinite(cents(data.adjustmentAmount))) return "优惠或退款金额格式不正确"
  if (data.actualPaymentAmount && !validPositiveAmount(data.actualPaymentAmount)) return "实际支付金额格式不正确"
  if (data.currency !== data.settlementCurrency && !validPositiveAmount(data.actualPaymentAmount) && !(Number(data.referenceRate) > 0)) return "请先补充本笔消费的汇率"
  return ""
}

function buildBillPayload(data) {
  const payer = data.payer.trim()
  const friend = data.friend.trim()
  const items = data.items.map((item, index) => ({
    item_id: item.id,
    name: item.name.trim(),
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
  const hasActualPayment = validPositiveAmount(data.actualPaymentAmount)
  return {
    title: data.title || defaultExpenseTitle(),
    participants: friend ? [payer, friend] : [payer],
    settlement_currency: data.settlementCurrency || data.currency,
    expenses: [{
      expense_id: data.expensePayloadId || `bill-${Date.now()}`,
      payer_id: payer,
      items,
      adjustments: adjustment,
      settlement_currency: data.settlementCurrency || data.currency,
      reference_rate: hasActualPayment ? undefined : data.referenceRate || undefined,
      reference_rate_source: hasActualPayment ? undefined : data.referenceRateSource || undefined,
      actual_payment: hasActualPayment ? { currency: data.actualPaymentCurrency, amount: data.actualPaymentAmount } : undefined,
    }],
  }
}

function settlementUrl(tripId, preview, result) {
  return `/pages/settlement/index?tripId=${encodeURIComponent(tripId)}&preview=${encodeURIComponent(JSON.stringify(preview))}&result=${encodeURIComponent(JSON.stringify(result))}`
}

function draftKey(tripId) { return `travel-split:expense-draft:${tripId}` }

Page({
  data: { tripId: "", expenseId: "", expensePayloadId: "", revision: 0, occurredAt: "", title: "", currency: "CNY", settlementCurrency: "CNY", settlementCurrencies: ["CNY", "JPY", "USD", "KRW"], settlementCurrencyIndex: 0, settlementCurrencyLocked: false, actualPaymentAmount: "", actualPaymentCurrency: "", actualPaymentCurrencyIndex: 0, referenceRate: "", referenceRateSource: "", rateEffectiveDate: "", rateStatus: "", estimatedSettlementAmount: "", checkoutLabel: "本次应付", checkoutAmount: "0.00", checkoutCurrency: "CNY", items: [newItem()], payer: "我", friend: "", memberOptions: [], memberLabels: [], payerIndex: 0, responsibilityRows: [], batchModeIndex: 0, batchModes: ["付款人自己买", "同行人自己买", "两人均分", "自定义比例"], batchPayerPercent: "50", batchFriendPercent: "50", receiptPath: "", ocrCandidates: [], taxAmount: "", taxIncluded: true, adjustmentAmount: "", adjustmentType: 0, adjustmentLabels: ["公共优惠 / 退款", "个人优惠", "后续退税"], ocrStatus: "未上传", saving: false, selectedCount: 0, billTotal: "0.00", sourceText: "", translatedText: "", amount: "", batchEditing: false, showBatchTools: false, showAdjustments: false, expandedItemId: "", reading: false },
  syncBill(changes = {}) {
    const next = { ...this.data, ...changes }
    const nextBillTotal = billTotal(next.items, next.taxAmount, next.taxIncluded, next.adjustmentAmount)
    const nextEstimate = validPositiveAmount(next.actualPaymentAmount) ? "" : estimatedSettlementAmount(nextBillTotal, next.referenceRate)
    const checkout = checkoutSummary({ ...next, billTotal: nextBillTotal, estimatedSettlementAmount: nextEstimate })
    const rows = responsibilityRows(next)
    const payerLabel = memberLabel(next.memberOptions, next.payer)
    this.setData({ ...changes, currencyIndex: Math.max(0, next.settlementCurrencies.indexOf(next.currency)), settlementCurrencyIndex: Math.max(0, next.settlementCurrencies.indexOf(next.settlementCurrency)), actualPaymentCurrencyIndex: Math.max(0, next.settlementCurrencies.indexOf(next.actualPaymentCurrency || next.settlementCurrency)), selectedCount: selectedItemCount(next.items), showBatchTools: Boolean(next.batchEditing && next.friend), billTotal: nextBillTotal, estimatedSettlementAmount: nextEstimate, checkoutLabel: checkout.label, checkoutAmount: checkout.amount, checkoutCurrency: checkout.currency, payerLabel, friendLabel: memberLabel(next.memberOptions, next.friend), responsibilityRows: rows, expenseAdvanceText: expenseAdvanceText({ ...next, billTotal: nextBillTotal, payerLabel, responsibilityRows: rows }) })
    if (next.tripId && !next.expenseId && wx.setStorageSync) wx.setStorageSync(draftKey(next.tripId), { ...next, receiptPath: "" })
  },
  onLoad(q) {
    const occurredAt = new Date().toISOString().slice(0, 10)
    const app = getApp()
    const receiptPath = q.receiptPath || app.globalData.pendingReceiptPath || ""
    app.globalData.pendingReceiptPath = ""
    const draft = !q.expenseId && wx.getStorageSync ? wx.getStorageSync(draftKey(q.tripId)) : null
    this.syncBill({ ...(draft || {}), tripId: q.tripId, currency: (draft && draft.currency) || q.currency || "CNY", settlementCurrency: (draft && draft.settlementCurrency) || q.currency || "CNY", expenseId: q.expenseId || "", reading: Boolean(q.expenseId), occurredAt: (draft && draft.occurredAt) || occurredAt, title: (draft && draft.title) || defaultExpenseTitle(), receiptPath, ocrStatus: receiptPath ? "等待上传识别" : "未上传" })
    const membersLoaded = typeof listTripMembers === "function" ? listTripMembers(q.tripId).then((members) => {
      const memberOptions = memberChoices(members)
      const current = memberOptions.find((option, index) => members[index] && members[index].is_current) || memberOptions[0]
      const payer = q.expenseId ? this.data.payer : (current && current.id || this.data.payer)
      this.syncBill({ memberOptions, memberLabels: memberOptions.map((option) => option.label), payer, friend: q.expenseId ? this.data.friend : (memberOptions.find((option) => !current || option.id !== current.id) || {}).id || "", payerIndex: Math.max(0, memberOptions.findIndex((option) => option.id === payer)) })
    }).catch(() => {}) : Promise.resolve()
    return membersLoaded.then(() => q.expenseId ? this.loadExpense(q.expenseId) : null).then(() => { if (receiptPath) this.startOcr(receiptPath) })
  },
  loadExpense(expenseId) {
    return listExpenses(this.data.tripId).then((records) => {
      const record = records.find((item) => item.id === expenseId)
      if (!record) return wx.showToast({ title: "消费记录不存在", icon: "none" })
      const hydrated = hydrateExpense(record)
      this.syncBill({ ...hydrated, payerIndex: Math.max(0, this.data.memberOptions.findIndex((option) => option.id === hydrated.payer)) })
    }).catch((error) => wx.showToast({ title: error.message || "读取消费失败", icon: "none" }))
  },
  beginEditing() { this.setData({ reading: false }) },
  onPayerMember(e) { const payerIndex = Number(e.detail.value); this.syncBill({ payerIndex, payer: this.data.memberOptions[payerIndex].id }) },
  chooseReceipt() { wx.chooseMedia({ count: 1, mediaType: ["image"], sourceType: ["camera", "album"], success: ({ tempFiles }) => wx.compressImage({ src: tempFiles[0].tempFilePath, quality: 80, success: ({ tempFilePath }) => { this.setData({ receiptPath: tempFilePath, ocrStatus: "等待上传识别" }); this.startOcr(tempFilePath) } }) }) },
  startOcr(filePath) { this.setData({ ocrStatus: "上传并识别中" }); uploadReceipt(this.data.tripId, filePath).then((job) => this.pollOcr(job.id)).catch(() => this.setData({ ocrStatus: "上传或识别失败，可手动录入" })) },
  pollOcr(jobId) { return getReceiptJob(jobId).then((job) => { const failedText = job.error_code === "OCR_TIMEOUT" ? "识别超时，可重试或手动录入" : "识别失败，可手动录入"; const labels = { queued: "排队识别中", processing: "正在识别", failed: failedText }; if (job.status === "needs_review") { const candidates = job.candidates || []; this.setData({ ocrStatus: `识别到 ${candidates.length} 项，等待核对`, ocrCandidates: candidates }); return wx.navigateTo({ url: `/pages/ocr-review/index?candidates=${encodeURIComponent(JSON.stringify(candidates))}`, events: { ocrCandidatesConfirmed: (confirmed) => this.applyOcrCandidates(confirmed) } }) } this.setData({ ocrStatus: labels[job.status] || job.status, ocrCandidates: [] }); if (job.status === "queued" || job.status === "processing") setTimeout(() => this.pollOcr(jobId), 1500) }).catch(() => this.setData({ ocrStatus: "识别状态查询失败，可手动录入" })) },
  applyOcrCandidates(candidates) {
    const recognized = (candidates || []).map((candidate) => ({ ...newItem(), name: String(candidate.translated_text || candidate.source_text || "").trim(), amount: String(candidate.amount || "").trim(), currency: String(candidate.currency || "").trim().toUpperCase() })).filter((item) => item.name && Number.isFinite(cents(item.amount)) && cents(item.amount) > 0)
    if (!recognized.length) return this.setData({ ocrCandidates: [], ocrStatus: "未识别到有效商品，请手动录入" })
    const starterIsBlank = this.data.items.length === 1 && !String(this.data.items[0].name || "").trim() && !String(this.data.items[0].amount || "").trim()
    const items = starterIsBlank ? recognized : [...this.data.items, ...recognized]
    const currencies = [...new Set(recognized.map((item) => item.currency).filter((currency) => /^[A-Z]{3}$/.test(currency)))]
    const currency = currencies.length === 1 ? currencies[0] : this.data.currency
    const notice = currencies.length > 1 ? "；币种不一致，请手动确认" : ""
    this.syncBill({ items, currency, ocrCandidates: [], ocrStatus: `已自动添加 ${recognized.length} 项，请核对金额${notice}`, expandedItemId: recognized[0].id })
    return this.refreshRate()
  },
  refreshRate() {
    const { actualPaymentAmount, currency, occurredAt, settlementCurrency } = this.data
    if (validPositiveAmount(actualPaymentAmount)) return Promise.resolve()
    if (currency === settlementCurrency) return Promise.resolve(this.syncBill({ referenceRate: "1", referenceRateSource: "same-currency", rateEffectiveDate: occurredAt, rateStatus: "无需换算" }))
    if (typeof getExchangeRate !== "function") return Promise.resolve()
    this.syncBill({ rateStatus: "正在查询汇率" })
    return getExchangeRate(occurredAt, currency, settlementCurrency)
      .then((quote) => this.syncBill({ referenceRate: quote.rate, referenceRateSource: quote.provider, rateEffectiveDate: quote.effective_date, rateStatus: "已自动获取" }))
      .catch(() => this.syncBill({ referenceRate: "", referenceRateSource: "", rateEffectiveDate: "", rateStatus: "failed" }))
  },
  toggleAdjustments() { this.setData({ showAdjustments: !this.data.showAdjustments }) },
  onTitle(e) { this.syncBill({ title: e.detail.value }) }, onOccurredAt(e) { this.syncBill({ occurredAt: e.detail.value }); return this.refreshRate() }, onPayer(e) { this.syncBill({ payer: e.detail.value }) }, onFriend(e) { this.syncBill({ friend: e.detail.value }) }, onTaxIncluded(e) { this.syncBill({ taxIncluded: e.detail.value }) }, onTax(e) { this.syncBill({ taxAmount: e.detail.value }) }, onAdjustment(e) { this.syncBill({ adjustmentAmount: e.detail.value }) }, onAdjustmentType(e) { this.syncBill({ adjustmentType: Number(e.detail.value) }) },
  onCurrency(e) { this.syncBill({ currency: this.data.settlementCurrencies[Number(e.detail.value)] }); return this.refreshRate() },
  onSettlementCurrency(e) { if (this.data.settlementCurrencyLocked) return; this.syncBill({ settlementCurrency: this.data.settlementCurrencies[Number(e.detail.value)] }); return this.refreshRate() },
  onActualPaymentAmount(e) {
    const next = applyActualPayment(this.data, e.detail.value, e.currentTarget.dataset.currency || this.data.actualPaymentCurrency || this.data.settlementCurrency)
    this.syncBill(next)
    this.refreshRate()
    return next.actualPaymentAmount
  },
  onActualPaymentCurrency(e) { this.syncBill(applyActualPayment(this.data, this.data.actualPaymentAmount, this.data.settlementCurrencies[Number(e.detail.value)])) },
  onManualRate(e) { this.syncBill({ referenceRate: e.detail.value, referenceRateSource: "manual", rateEffectiveDate: this.data.occurredAt, rateStatus: "已手动填写" }) },
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
    const wasEditing = Boolean(this.data.expenseId)
    return save
      .then((saved) => { if (wx.removeStorageSync) wx.removeStorageSync(draftKey(this.data.tripId)); this.syncBill({ expenseId: saved.id, revision: saved.revision, occurredAt: saved.occurred_at }); if (wasEditing) { wx.navigateBack(); return null } return listExpenses(this.data.tripId).then((records) => records.map((record) => record.id === saved.id ? saved : record)) })
      .then((records) => {
        if (wasEditing) return null
        const tripPreview = buildTripPreview(records, this.data.currency)
        if (!tripPreview) throw new Error("请先统一每笔消费的两位同行人")
        return previewSettlement(this.data.tripId, tripPreview).then((result) => ({ result, tripPreview }))
      })
      .then((preview) => preview && wx.redirectTo({ url: settlementUrl(this.data.tripId, preview.tripPreview, preview.result) }))
      .catch((error) => wx.showToast({ title: error.message || "保存账单失败", icon: "none" }))
      .finally(() => this.setData({ saving: false }))
  },
})

if (typeof module !== "undefined") module.exports = { allocationFor, applyActualPayment, billTotal, buildBillPayload, buildTripPreview, checkoutSummary, defaultExpenseTitle, estimatedSettlementAmount, expenseAdvanceText, hydrateExpense, memberChoices, responsibilityRows, selectedItemCount, validPositiveAmount, validateBill }
