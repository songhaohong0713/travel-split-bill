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

function loadExpensePage(api, wx = {}) {
  const source = fs.readFileSync(path.join(__dirname, "..", "pages", "expense", "index.js"), "utf8")
  let definition
  const module = { exports: {} }
  vm.runInNewContext(source, {
    Page(value) { definition = value },
    require() { return api },
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
})

test("expense helpers provide a dated title, hydrate a record, and combine a trip preview", () => {
  const { helpers } = loadExpensePage({})
  const recordA = {
    id: "expense-a", revision: 2, occurred_at: "2026-09-27",
    payload: { title: "机场晚餐", participants: ["我", "小王"], settlement_currency: "CNY", expenses: [{ expense_id: "a", payer_id: "我", items: [], adjustments: [] }] },
  }
  const recordB = {
    id: "expense-b", revision: 1, occurred_at: "2026-09-28",
    payload: { title: "便利店", participants: ["我", "小王"], settlement_currency: "CNY", expenses: [{ expense_id: "b", payer_id: "小王", items: [], adjustments: [] }] },
  }

  assert.equal(helpers.defaultExpenseTitle(new Date("2026-09-27T08:00:00")), "9月27日消费")
  assert.equal(helpers.hydrateExpense(recordA).title, "机场晚餐")
  assert.equal(helpers.buildTripPreview([recordA, recordB], "CNY").expenses.length, 2)
})

test("validateBill rejects custom shares that are not 100 percent", () => {
  const { helpers } = loadExpensePage({})
  assert.match(helpers.validateBill({ payer: "我", friend: "小王", items: [{ name: "餐费", amount: "20", allocationMode: "custom", payerPercent: "70", friendPercent: "20" }] }), /100%/)
})

test("saveAndPreview persists then settles every expense in the trip", async () => {
  const calls = []
  const { definition } = loadExpensePage({
    createExpense() { calls.push("save"); return Promise.resolve({ id: "expense-1", payload: { participants: ["我", "小王"], expenses: [{ expense_id: "expense-1", payer_id: "我", items: [], adjustments: [] }] } }) },
    listExpenses() { calls.push("list"); return Promise.resolve([{ id: "expense-1", payload: { participants: ["我", "小王"], expenses: [{ expense_id: "expense-1", payer_id: "我", items: [], adjustments: [] }] } }, { id: "expense-2", payload: { participants: ["我", "小王"], expenses: [{ expense_id: "expense-2", payer_id: "小王", items: [], adjustments: [] }] } }]) },
    previewSettlement() { calls.push("preview"); return Promise.resolve({ transfers: [] }) },
    uploadReceipt() {}, getReceiptJob() {},
  }, { navigateTo() {} })
  const instance = pageInstance(definition, {
    tripId: "trip-1", currency: "CNY", payer: "我", friend: "小王",
    items: [{ id: "a", name: "晚餐", amount: "20", allocationMode: "split", payerPercent: "50", friendPercent: "50", selected: false }],
  })
  await instance.saveAndPreview()
  assert.deepEqual(calls, ["save", "list", "preview"])
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

test("expense page exposes receipt-book layout hooks", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "..", "pages", "expense", "index.wxml"), "utf8")
  assert.match(wxml, /class="bill-summary"/)
  assert.match(wxml, /class="batch-bar"/)
  assert.match(wxml, /class="checkout-bar"/)
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

test("expense page exposes compact item disclosure", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "..", "pages", "expense", "index.wxml"), "utf8")
  assert.match(wxml, /toggleItemDetail/)
  assert.match(wxml, /expandedItemId/)
})
