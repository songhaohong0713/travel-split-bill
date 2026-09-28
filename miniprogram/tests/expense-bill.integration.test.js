const assert = require("node:assert/strict")
const fs = require("node:fs")
const path = require("node:path")
const test = require("node:test")
const vm = require("node:vm")

function loadApi(calls) {
  const source = fs.readFileSync(path.join(__dirname, "..", "services", "api.js"), "utf8")
  const module = { exports: {} }
  vm.runInNewContext(source, {
    getApp() { return { globalData: { apiBaseUrl: "https://example.test" } } },
    wx: {
      getStorageSync() { return "" },
      request(options) {
        calls.push(options)
        options.success({ statusCode: 201, data: { data: { id: "expense-1" } } })
      },
    },
    module,
    exports: module.exports,
    Promise,
  })
  return module.exports
}

test("createExpense adds a UUID idempotency key", async () => {
  const calls = []
  const api = loadApi(calls)

  await api.createExpense("trip-1", "2026-09-18", { items: [] })

  assert.equal(calls[0].url, "https://example.test/v1/trips/trip-1/expenses")
  assert.equal(calls[0].method, "POST")
  assert.equal(calls[0].data.occurred_at, "2026-09-18")
  assert.match(calls[0].header["Idempotency-Key"], /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i)
})

test("expense API reads a trip list and updates a revision", async () => {
  const calls = []
  const api = loadApi(calls)

  await api.listExpenses("trip-1")
  await api.updateExpense("trip-1", "expense-1", 2, "2026-09-27", { title: "晚餐" })

  assert.equal(calls[0].url, "https://example.test/v1/trips/trip-1/expenses")
  assert.equal(calls[0].method, "GET")
  assert.equal(calls[1].url, "https://example.test/v1/trips/trip-1/expenses/expense-1")
  assert.equal(calls[1].method, "PATCH")
  assert.deepEqual(JSON.parse(JSON.stringify(calls[1].data)), {
    revision: 2,
    occurred_at: "2026-09-27",
    payload: { title: "晚餐" },
  })
})

function loadExpensePage(api, wx = {}, app = { globalData: {} }) {
  const source = fs.readFileSync(path.join(__dirname, "..", "pages", "expense", "index.js"), "utf8")
  let definition
  const module = { exports: {} }
  vm.runInNewContext(source, {
    Page(value) { definition = value },
    require() { return api },
    getApp() { return app },
    wx: { showToast() {}, ...wx },
    setTimeout() {},
    module,
    exports: module.exports,
    Promise,
    Date,
    Math,
  })
  return { definition, helpers: module.exports }
}

function pageInstance(definition, data = {}) {
  return { ...definition, data: { ...definition.data, ...data }, setData(value) { this.data = { ...this.data, ...value } } }
}

test("buildBillPayload keeps per-item allocations", () => {
  const { helpers } = loadExpensePage({})
  const result = helpers.buildBillPayload({
    currency: "CNY", payer: "我", friend: "小王", items: [
      { id: "a", name: "晚餐", amount: "20", allocationMode: "split", payerPercent: "50", friendPercent: "50" },
      { id: "b", name: "甜点", amount: "10", allocationMode: "friend", payerPercent: "0", friendPercent: "100" },
    ], taxAmount: "", adjustmentAmount: "", adjustmentType: 0,
  })
  assert.deepEqual(JSON.parse(JSON.stringify(result.expenses[0].items.map((item) => item.allocation))), [
    { "我": "0.5", "小王": "0.5" }, { "小王": "1" },
  ])
  assert.deepEqual(JSON.parse(JSON.stringify(result.expenses[0].items.map((item) => item.name))), ["晚餐", "甜点"])
})

test("checkout summary uses settlement currency when a rate is available", () => {
  const { helpers } = loadExpensePage({})
  assert.deepEqual(JSON.parse(JSON.stringify(helpers.checkoutSummary({ billTotal: "5000.00", currency: "JPY", settlementCurrency: "CNY", estimatedSettlementAmount: "212.95", actualPaymentAmount: "", actualPaymentCurrency: "" }))), { label: "预计结算", amount: "212.95", currency: "CNY" })
})

test("checkout summary prioritizes an actual payment", () => {
  const { helpers } = loadExpensePage({})
  assert.deepEqual(JSON.parse(JSON.stringify(helpers.checkoutSummary({ billTotal: "5000.00", currency: "JPY", settlementCurrency: "CNY", estimatedSettlementAmount: "212.95", actualPaymentAmount: "212", actualPaymentCurrency: "CNY" }))), { label: "实际支付", amount: "212", currency: "CNY" })
})

test("manual currency selection makes saved item amounts use JPY", () => {
  const { definition, helpers } = loadExpensePage({})
  const instance = pageInstance(definition, { currency: "CNY", settlementCurrency: "JPY", items: [{ id: "a", name: "晚餐", amount: "1200" }] })

  instance.onCurrency({ detail: { value: 1 } })

  assert.equal(instance.data.currency, "JPY")
  assert.equal(helpers.buildBillPayload({ ...instance.data, payer: "我", friend: "", taxAmount: "", taxIncluded: true, adjustmentAmount: "", adjustmentType: 0 }).expenses[0].items[0].amount.currency, "JPY")
})

test("expense helpers provide a dated title, hydrate a record, and combine a trip preview", () => {
  const { helpers } = loadExpensePage({})
  const recordA = {
    id: "expense-a", revision: 2, occurred_at: "2026-09-27",
    payload: { title: "机场晚餐", participants: ["我", "小王"], settlement_currency: "CNY", expenses: [{ expense_id: "a", payer_id: "我", items: [{ item_id: "a-item", amount: { amount: "20", currency: "CNY" }, allocation: { 我: "1" } }], adjustments: [] }] },
  }
  const recordB = {
    id: "expense-b", revision: 1, occurred_at: "2026-09-28",
    payload: { title: "便利店", participants: ["我", "小王"], settlement_currency: "CNY", expenses: [{ expense_id: "b", payer_id: "小王", items: [{ item_id: "b-item", amount: { amount: "10", currency: "CNY" }, allocation: { 小王: "1" } }], adjustments: [] }] },
  }

  assert.equal(helpers.defaultExpenseTitle(new Date("2026-09-27T08:00:00")), "9月27日消费")
  assert.equal(helpers.hydrateExpense(recordA).title, "机场晚餐")
  assert.equal(helpers.buildTripPreview([recordA, recordB], "CNY").expenses.length, 2)
})

test("trip preview ignores legacy empty expenses and invalid actual payments", () => {
  const { helpers } = loadExpensePage({})
  const preview = helpers.buildTripPreview([
    { payload: { participants: ["我", "卢", ""], expenses: [
      { expense_id: "legacy-invalid", payer_id: "我", settlement_currency: "JPY", actual_payment: { amount: "undefine", currency: "JPY" }, items: [{ item_id: "tea", amount: { amount: "120", currency: "JPY" }, allocation: { 我: "1" } }] },
      { expense_id: "legacy-empty", payer_id: "我", items: [] },
    ] } },
    { payload: { participants: ["我", "卢"], expenses: [{ expense_id: "empty-again", payer_id: "我", items: [] }] } },
  ], "JPY")

  assert.deepEqual(JSON.parse(JSON.stringify(preview.participants)), ["我", "卢"])
  assert.equal(preview.expenses.length, 1)
  assert.equal(preview.expenses[0].actual_payment, undefined)
})

test("expense page starts OCR for a receipt selected before navigation", async () => {
  const app = { globalData: { pendingReceiptPath: "wxfile://receipt.jpg" } }
  const { definition } = loadExpensePage({}, {}, app)
  const instance = pageInstance(definition)
  let receivedPath = ""
  instance.startOcr = (filePath) => { receivedPath = filePath }

  await instance.onLoad({ tripId: "trip-1", currency: "JPY" })

  assert.equal(instance.data.receiptPath, "wxfile://receipt.jpg")
  assert.equal(instance.data.ocrStatus, "等待上传识别")
  assert.equal(receivedPath, "wxfile://receipt.jpg")
  assert.equal(app.globalData.pendingReceiptPath, "")
})

test("saved expense opens in reading mode until edit is requested", async () => {
  const { definition } = loadExpensePage({
    listExpenses() { return Promise.resolve([{ id: "expense-1", revision: 1, occurred_at: "2026-09-28", payload: { title: "便利店", participants: ["我"], expenses: [{ payer_id: "我", items: [{ item_id: "tea", name: "绿茶", amount: { amount: "20", currency: "CNY" }, allocation: { 我: "1" } }] }] } }]) },
  })
  const instance = pageInstance(definition)
  await instance.onLoad({ tripId: "trip-1", expenseId: "expense-1", currency: "CNY" })
  assert.equal(instance.data.reading, true)
  instance.beginEditing()
  assert.equal(instance.data.reading, false)
})

test("validateBill rejects custom shares that are not 100 percent", () => {
  const { helpers } = loadExpensePage({})
  assert.match(helpers.validateBill({ payer: "我", friend: "小王", items: [{ name: "餐费", amount: "20", allocationMode: "custom", payerPercent: "70", friendPercent: "20" }] }), /100%/)
})

test("saveAndPreview persists then settles every expense in the trip", async () => {
  const calls = []
  let redirected = ""
  const { definition } = loadExpensePage({
    createExpense() { calls.push("save"); return Promise.resolve({ id: "expense-1", payload: { participants: ["我", "小王"], expenses: [{ expense_id: "expense-1", payer_id: "我", items: [{ item_id: "a", amount: { amount: "20", currency: "CNY" }, allocation: { 我: "1" } }], adjustments: [] }] } }) },
    listExpenses() { calls.push("list"); return Promise.resolve([{ id: "expense-1", payload: { participants: ["我", "小王"], expenses: [{ expense_id: "expense-1", payer_id: "我", items: [{ item_id: "a", amount: { amount: "20", currency: "CNY" }, allocation: { 我: "1" } }], adjustments: [] }] } }, { id: "expense-2", payload: { participants: ["我", "小王"], expenses: [{ expense_id: "expense-2", payer_id: "小王", items: [{ item_id: "b", amount: { amount: "10", currency: "CNY" }, allocation: { 小王: "1" } }], adjustments: [] }] } }]) },
    previewSettlement() { calls.push("preview"); return Promise.resolve({ transfers: [] }) },
    uploadReceipt() {}, getReceiptJob() {},
  }, { redirectTo({ url }) { redirected = url } })
  const instance = pageInstance(definition, {
    tripId: "trip-1", currency: "CNY", payer: "我", friend: "小王",
    items: [{ id: "a", name: "晚餐", amount: "20", allocationMode: "split", payerPercent: "50", friendPercent: "50", selected: false }],
  })
  await instance.saveAndPreview()
  assert.deepEqual(calls, ["save", "list", "preview"])
  assert.match(redirected, /^\/pages\/settlement\/index\?tripId=trip-1/)
})

test("expense API deletes a trip and one expense", async () => {
  const calls = []
  const api = loadApi(calls)

  await api.deleteExpense("trip-1", "expense-1")
  await api.deleteTrip("trip-1")

  assert.equal(calls[0].method, "DELETE")
  assert.equal(calls[0].url, "https://example.test/v1/trips/trip-1/expenses/expense-1")
  assert.equal(calls[1].method, "DELETE")
  assert.equal(calls[1].url, "https://example.test/v1/trips/trip-1")
})

test("a successful update keeps the latest revision for the next edit", async () => {
  const { definition } = loadExpensePage({
    updateExpense() { return Promise.resolve({ id: "expense-1", revision: 2, occurred_at: "2026-09-28", payload: { participants: ["我"], expenses: [{ expense_id: "expense-1", payer_id: "我", items: [], adjustments: [] }] } }) },
    listExpenses() { return Promise.resolve([{ id: "expense-1", revision: 2, payload: { participants: ["我"], expenses: [{ expense_id: "expense-1", payer_id: "我", items: [], adjustments: [] }] } }]) },
    previewSettlement() { return Promise.resolve({ transfers: [] }) }, uploadReceipt() {}, getReceiptJob() {},
  }, { navigateTo() {} })
  const instance = pageInstance(definition, { tripId: "trip-1", expenseId: "expense-1", revision: 1, occurredAt: "2026-09-28", currency: "CNY", settlementCurrency: "CNY", payer: "我", friend: "", items: [{ id: "a", name: "晚餐", amount: "20", allocationMode: "payer", payerPercent: "100", friendPercent: "0", selected: false }] })

  await instance.saveAndPreview()

  assert.equal(instance.data.revision, 2)
})

test("a personal expense needs no companion and estimates settlement currency", () => {
  const { helpers } = loadExpensePage({})
  const payload = helpers.buildBillPayload({ currency: "JPY", settlementCurrency: "CNY", payer: "我", friend: "", items: [{ id: "a", name: "晚餐", amount: "6155", allocationMode: "payer", payerPercent: "100", friendPercent: "0" }], taxAmount: "", taxIncluded: true, adjustmentAmount: "", adjustmentType: 0 })

  assert.equal(helpers.validateBill({ payer: "我", friend: "", currency: "CNY", settlementCurrency: "CNY", items: [{ name: "晚餐", amount: "20", allocationMode: "payer", payerPercent: "100", friendPercent: "0" }] }), "")
  assert.deepEqual(JSON.parse(JSON.stringify(payload.participants)), ["我"])
  assert.deepEqual(JSON.parse(JSON.stringify(payload.expenses[0].items[0].allocation)), { "我": "1" })
  assert.equal(helpers.estimatedSettlementAmount("6155.00", "0.04254"), "261.83")
})

test("batch allocation changes only selected items", () => {
  const { definition } = loadExpensePage({}, { showToast() {} })
  const instance = pageInstance(definition, {
    batchModeIndex: 2,
    items: [
      { id: "a", selected: true, allocationMode: "payer", payerPercent: "100", friendPercent: "0" },
      { id: "b", selected: false, allocationMode: "friend", payerPercent: "0", friendPercent: "100" },
    ],
  })
  instance.applyBatchAllocation()
  assert.equal(instance.data.items[0].allocationMode, "split")
  assert.equal(instance.data.items[1].allocationMode, "friend")
})

test("batch selection stays hidden until explicit batch editing begins", () => {
  const { definition } = loadExpensePage({}, { showToast() {} })
  const instance = pageInstance(definition, { friend: "小王", items: [{ id: "a", selected: true, amount: "20" }] })

  assert.equal(instance.data.batchEditing, false)
  assert.equal(instance.data.showBatchTools, false)
  instance.toggleBatchEditing()
  assert.equal(instance.data.batchEditing, true)
  assert.equal(instance.data.showBatchTools, true)
})

test("expense page uses a compact inline toolbar for batch allocation", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "..", "pages", "expense", "index.wxml"), "utf8")
  assert.match(wxml, /class="batch-toolbar"/)
  assert.doesNotMatch(wxml, /class="batch-bar"/)
  assert.match(wxml, /已选 {{selectedCount}} 项/)
})

test("batch editing uses compact checkboxes instead of item switches", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "..", "pages", "expense", "index.wxml"), "utf8")
  assert.match(wxml, /<checkbox wx:if="{{batchEditing}}"/)
  assert.doesNotMatch(wxml, /<switch wx:if="{{batchEditing}}"/)
})

test("buildBillPayload applies a bill tax only once", () => {
  const { helpers } = loadExpensePage({})
  const result = helpers.buildBillPayload({
    currency: "CNY", payer: "我", friend: "小王", taxAmount: "2", taxIncluded: false, adjustmentAmount: "", adjustmentType: 0,
    items: [
      { id: "a", name: "晚餐", amount: "20", allocationMode: "split", payerPercent: "50", friendPercent: "50" },
      { id: "b", name: "甜点", amount: "10", allocationMode: "split", payerPercent: "50", friendPercent: "50" },
    ],
  })
  assert.equal(result.expenses[0].items.filter((item) => item.tax_amount).length, 1)
  assert.equal(result.expenses[0].items.find((item) => item.tax_amount).tax_amount.amount, "2.00")
})

test("expense payload saves a selected settlement currency and historical rate", () => {
  const { helpers } = loadExpensePage({})
  const payload = helpers.buildBillPayload({
    tripId: "trip-1", title: "便利店", currency: "JPY", settlementCurrency: "CNY", referenceRate: "0.04762", referenceRateSource: "frankfurter", payer: "我", friend: "小王", items: [{ id: "tea", name: "茶", amount: "1000", allocationMode: "split", payerPercent: "50", friendPercent: "50" }], taxAmount: "", taxIncluded: true, adjustmentAmount: "", adjustmentType: 0,
  })
  assert.equal(payload.expenses[0].settlement_currency, "CNY")
  assert.equal(payload.expenses[0].reference_rate, "0.04762")
  assert.equal(payload.expenses[0].reference_rate_source, "frankfurter")
  assert.equal(payload.expenses[0].actual_payment, undefined)
})

test("actual payment locks the expense settlement currency", () => {
  const { helpers } = loadExpensePage({})
  const next = helpers.applyActualPayment({ settlementCurrency: "JPY" }, "140.82", "CNY")
  assert.equal(next.settlementCurrency, "CNY")
  assert.equal(next.settlementCurrencyLocked, true)
})

test("actual payment ignores undefined-like input values", () => {
  const { helpers } = loadExpensePage({})
  const next = helpers.applyActualPayment({ settlementCurrency: "JPY" }, "undefined", "CNY")
  assert.equal(next.actualPaymentAmount, "")
  assert.equal(next.actualPaymentCurrency, "")
  assert.equal(next.settlementCurrencyLocked, false)
})

test("actual payment removes an undefined prefix before accepting an iOS input digit", () => {
  const { helpers } = loadExpensePage({})
  const next = helpers.applyActualPayment({ settlementCurrency: "JPY" }, "undefined1", "JPY")
  assert.equal(next.actualPaymentAmount, "1")
  assert.equal(next.actualPaymentCurrency, "JPY")
  assert.equal(next.settlementCurrency, "JPY")
})

test("actual payment returns its normalized value to the native iOS input", () => {
  const { definition } = loadExpensePage({}, { showToast() {} })
  const instance = pageInstance(definition, { settlementCurrency: "JPY", actualPaymentCurrency: "" })
  const value = instance.onActualPaymentAmount({ detail: { value: "undefined6" }, currentTarget: { dataset: { currency: "JPY" } } })
  assert.equal(value, "6")
  assert.equal(instance.data.actualPaymentAmount, "6")
  assert.equal(instance.data.actualPaymentCurrency, "JPY")
})

test("invalid historical actual payment is cleared and never submitted", () => {
  const { helpers } = loadExpensePage({})
  const hydrated = helpers.hydrateExpense({
    id: "expense-1", revision: 1, occurred_at: "2026-09-28",
    payload: {
      participants: ["我", "卢"], settlement_currency: "JPY",
      expenses: [{
        expense_id: "bill-1", settlement_currency: "JPY", actual_payment: { amount: "undefine", currency: "JPY" },
        items: [{ item_id: "item-1", name: "晚餐", amount: { amount: "100", currency: "JPY" }, allocation: { 我: "1" } }],
      }],
    },
  })
  assert.equal(hydrated.actualPaymentAmount, "")
  assert.equal(hydrated.settlementCurrencyLocked, false)

  const bill = {
    payer: "我", friend: "卢", currency: "JPY", settlementCurrency: "JPY", referenceRate: "", actualPaymentAmount: "undefine", actualPaymentCurrency: "JPY",
    items: [{ id: "item-1", name: "晚餐", amount: "100", allocationMode: "payer", payerPercent: "100", friendPercent: "0" }],
    taxAmount: "", taxIncluded: true, adjustmentAmount: "", adjustmentType: 0,
  }
  assert.match(helpers.validateBill(bill), /实际支付金额格式不正确/)
  assert.equal(helpers.buildBillPayload(bill).expenses[0].actual_payment, undefined)
})

test("bill summary counts selected items and includes adjustments", () => {
  const { helpers } = loadExpensePage({})
  const items = [
    { selected: true, amount: "20" },
    { selected: false, amount: "10" },
  ]
  assert.equal(helpers.selectedItemCount(items), 1)
  assert.equal(helpers.billTotal(items, "2", false, "-3"), "29.00")
})

test("item selection refreshes the visual bill summary", () => {
  const { definition } = loadExpensePage({}, { showToast() {} })
  const instance = pageInstance(definition, {
    items: [{ id: "a", selected: false, amount: "20" }],
    taxAmount: "", taxIncluded: true, adjustmentAmount: "",
  })
  instance.toggleItem({ currentTarget: { dataset: { id: "a" } } })
  assert.equal(instance.data.selectedCount, 1)
  assert.equal(instance.data.billTotal, "20.00")
})

test("receipt recognition replaces the blank starter item with every valid item", () => {
  const { definition } = loadExpensePage({}, { showToast() {} })
  const instance = pageInstance(definition, {
    items: [{ id: "blank", name: "", amount: "", selected: false, allocationMode: "payer", payerPercent: "100", friendPercent: "0" }],
    taxAmount: "", taxIncluded: true, adjustmentAmount: "",
  })

  instance.applyOcrCandidates([
    { source_text: "お茶", translated_text: "茶", amount: "120" },
    { source_text: "牛乳", translated_text: "牛奶", amount: "230" },
  ])

  assert.deepEqual(JSON.parse(JSON.stringify(instance.data.items.map(({ name, amount }) => ({ name, amount })))), [
    { name: "茶", amount: "120" },
    { name: "牛奶", amount: "230" },
  ])
  assert.match(instance.data.ocrStatus, /已自动添加 2 项/)
})

test("receipt recognition appends items without overwriting manual details", () => {
  const { definition } = loadExpensePage({}, { showToast() {} })
  const instance = pageInstance(definition, {
    items: [{ id: "manual", name: "手动商品", amount: "10", selected: false, allocationMode: "payer", payerPercent: "100", friendPercent: "0" }],
    taxAmount: "", taxIncluded: true, adjustmentAmount: "",
  })

  instance.applyOcrCandidates([{ source_text: "お茶", translated_text: "茶", amount: "120" }])

  assert.equal(instance.data.items.length, 2)
  assert.equal(instance.data.items[0].name, "手动商品")
  assert.equal(instance.data.items[1].name, "茶")
})

test("receipt recognition switches the expense currency when all items agree", () => {
  const { definition } = loadExpensePage({}, { showToast() {} })
  const instance = pageInstance(definition, {
    currency: "CNY", items: [{ id: "blank", name: "", amount: "", selected: false, allocationMode: "payer", payerPercent: "100", friendPercent: "0" }],
    taxAmount: "", taxIncluded: true, adjustmentAmount: "",
  })

  instance.applyOcrCandidates([{ source_text: "緑茶", translated_text: "绿茶", amount: "160", currency: "JPY" }])

  assert.equal(instance.data.currency, "JPY")
})

test("receipt recognition keeps the selected currency for mixed currencies", () => {
  const { definition } = loadExpensePage({}, { showToast() {} })
  const instance = pageInstance(definition, {
    currency: "CNY", items: [{ id: "blank", name: "", amount: "", selected: false, allocationMode: "payer", payerPercent: "100", friendPercent: "0" }],
    taxAmount: "", taxIncluded: true, adjustmentAmount: "",
  })

  instance.applyOcrCandidates([
    { source_text: "Tea", translated_text: "茶", amount: "10", currency: "JPY" },
    { source_text: "Coffee", translated_text: "咖啡", amount: "10", currency: "USD" },
  ])

  assert.equal(instance.data.currency, "CNY")
  assert.match(instance.data.ocrStatus, /币种不一致/)
})

test("expense page exposes receipt-book layout hooks", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "..", "pages", "expense", "index.wxml"), "utf8")
  assert.match(wxml, /class="bill-summary"/)
  assert.match(wxml, /class="batch-toolbar"/)
  assert.match(wxml, /class="checkout-bar"/)
})

test("expense page exposes manual currency selection and explicit save copy", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "..", "pages", "expense", "index.wxml"), "utf8")
  assert.match(wxml, /bindchange="onCurrency"/)
  assert.match(wxml, /保存并查看分账/)
})

test("settlement page exposes settlement-sheet layout hooks", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "..", "pages", "settlement", "index.wxml"), "utf8")
  assert.match(wxml, /class="[^"]*settlement-sheet"/)
  assert.match(wxml, /class="settlement-hero"/)
  assert.match(wxml, /已经结清/)
})

test("checkout bar stays in document flow so it cannot cover form controls", () => {
  const wxss = fs.readFileSync(path.join(__dirname, "..", "pages", "expense", "index.wxss"), "utf8")
  const checkoutRule = wxss.match(/\.checkout-bar\s*\{[^}]*\}/)[0]
  assert.doesNotMatch(checkoutRule, /position:\s*fixed/)
})

test("expense page keeps optional tools collapsed by default", () => {
  const { definition } = loadExpensePage({})
  assert.equal(definition.data.showAdjustments, false)
  assert.equal(definition.data.showBatchTools, false)
})

test("refund adjustment accepts a negative amount on iOS", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "..", "pages", "expense", "index.wxml"), "utf8")
  assert.match(wxml, /placeholder="负数，例如 -20" value="{{adjustmentAmount}}" bindinput="onAdjustment"/)
  assert.doesNotMatch(wxml, /type="digit" placeholder="负数，例如 -20"/)
})

test("actual payment input binds an empty fallback and the displayed currency", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "..", "pages", "expense", "index.wxml"), "utf8")
  assert.match(wxml, /value="{{actualPaymentAmount \|\| ''}}"/)
  assert.match(wxml, /data-currency="{{settlementCurrency}}"/)
})

test("expense page exposes compact item disclosure", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "..", "pages", "expense", "index.wxml"), "utf8")
  assert.match(wxml, /toggleItemDetail/)
  assert.match(wxml, /expandedItemId/)
})

test("settlement page keeps publishing and sharing in a distinct settlement sheet", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "..", "pages", "settlement", "index.wxml"), "utf8")
  assert.match(wxml, /class="settlement-summary"/)
  assert.match(wxml, /确认并发布/)
  assert.match(wxml, /创建并复制链接/)
})

test("expense page renders items as one dense ledger and exposes the subtotal", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "..", "pages", "expense", "index.wxml"), "utf8")
  assert.match(wxml, /class="item-ledger"/)
  assert.match(wxml, /class="ledger-row"/)
  assert.match(wxml, /本次消费小计/)
  assert.match(wxml, /toggleBatchEditing/)
})

test("settlement page renders a transfer section per currency", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "..", "pages", "settlement", "index.wxml"), "utf8")
  assert.match(wxml, /wx:for="{{currencyGroups}}"/)
  assert.match(wxml, /{{item.currency}} 结算/)
})

test("expense page shows settlement currency and rate after the subtotal", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "..", "pages", "expense", "index.wxml"), "utf8")
  assert.match(wxml, /class="expense-subtotal"[\s\S]*class="expense-settlement"/)
  assert.match(wxml, /实际支付金额（可选）/)
  assert.match(wxml, /重新查询/)
})

test("expanded item editor stops row toggle events from swallowing input taps", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "..", "pages", "expense", "index.wxml"), "utf8")
  assert.match(wxml, /class="item-editor" catchtap="stopItemEditorTap"/)
})
