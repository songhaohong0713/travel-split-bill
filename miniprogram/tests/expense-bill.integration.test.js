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

test("validateBill rejects custom shares that are not 100 percent", () => {
  const { helpers } = loadExpensePage({})
  assert.match(helpers.validateBill({ payer: "我", friend: "小王", items: [{ name: "餐费", amount: "20", allocationMode: "custom", payerPercent: "70", friendPercent: "20" }] }), /100%/)
})

test("saveAndPreview persists the bill before previewing", async () => {
  const calls = []
  const { definition } = loadExpensePage({
    createExpense() { calls.push("save"); return Promise.resolve({ id: "expense-1" }) },
    previewSettlement() { calls.push("preview"); return Promise.resolve({ transfers: [] }) },
    uploadReceipt() {}, getReceiptJob() {},
  }, { navigateTo() {} })
  const instance = pageInstance(definition, {
    tripId: "trip-1", currency: "CNY", payer: "我", friend: "小王",
    items: [{ id: "a", name: "晚餐", amount: "20", allocationMode: "split", payerPercent: "50", friendPercent: "50", selected: false }],
  })
  await instance.saveAndPreview()
  assert.deepEqual(calls, ["save", "preview"])
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
