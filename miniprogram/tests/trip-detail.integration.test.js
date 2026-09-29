const assert = require("node:assert/strict")
const fs = require("node:fs")
const path = require("node:path")
const test = require("node:test")
const vm = require("node:vm")

function loadTripDetail(api, wx = {}, app = { globalData: {} }) {
  const source = fs.readFileSync(path.join(__dirname, "..", "pages", "trip-detail", "index.js"), "utf8")
  let definition
  vm.runInNewContext(source, {
    Page(value) { definition = value },
    require() { return api },
    getApp() { return app },
    wx: { showToast() {}, navigateTo() {}, ...wx },
    Date,
    Math,
    Promise,
  })
  return definition
}

function pageInstance(definition, data = {}) {
  return { ...definition, data: { ...definition.data, ...data }, setData(value) { this.data = { ...this.data, ...value } } }
}

test("trip detail lists saved expenses as compact summaries", async () => {
  const definition = loadTripDetail({
    listExpenses() {
      return Promise.resolve([{
        id: "expense-1", revision: 1, occurred_at: "2026-09-27",
        payload: { title: "第一晚晚餐", expenses: [{ payer_id: "我", items: [{ amount: { amount: "20.00" } }, { amount: { amount: "10.00" } }] }] },
      }])
    },
  })
  const page = pageInstance(definition)

  await page.onLoad({ tripId: "trip-1", currency: "CNY", name: "东京周末" })

  assert.equal(page.data.expenses[0].title, "第一晚晚餐")
  assert.equal(page.data.expenses[0].itemCount, 2)
  assert.equal(page.data.expenses[0].total, "30.00")
})

test("trip detail decodes an encoded travel title", async () => {
  const definition = loadTripDetail({ listExpenses: () => Promise.resolve([]) })
  const page = pageInstance(definition)

  await page.onLoad({ tripId: "trip-1", currency: "CNY", name: "%E4%B8%9C%E4%BA%AC%E6%97%85%E8%A1%8C" })

  assert.equal(page.data.name, "东京旅行")
})

test("expense summary prioritizes actual payment and keeps the original item subtotal", () => {
  const record = {
    id: "expense-1", revision: 1, occurred_at: "2026-09-29",
    payload: { title: "便利店", expenses: [{
      payer_id: "我",
      actual_payment: { amount: "54.8", currency: "CNY" },
      items: [{ amount: { amount: "1287", currency: "JPY" } }],
    }] },
  }
  const helpers = (() => {
    const source = fs.readFileSync(path.join(__dirname, "..", "pages", "trip-detail", "index.js"), "utf8")
    const localModule = { exports: {} }
    vm.runInNewContext(source, { Page() {}, require() { return {} }, module: localModule, exports: localModule.exports, wx: {}, Date, Math, Promise })
    return localModule.exports
  })()
  const result = helpers.expenseSummary(record, "CNY")

  assert.equal(result.total, "54.8")
  assert.equal(result.currency, "CNY")
  assert.equal(result.amountLabel, "实际支付")
  assert.equal(result.originalTotal, "1287.00")
  assert.equal(result.originalCurrency, "JPY")
})

test("trip detail derives a same-currency overview without mixing other currencies", async () => {
  const definition = loadTripDetail({ listExpenses: () => Promise.resolve([
    { id: "a", occurred_at: "2026-09-27", payload: { expenses: [{ items: [{ amount: { amount: "20", currency: "CNY" } }] }] } },
    { id: "b", occurred_at: "2026-09-28", payload: { expenses: [{ items: [{ amount: { amount: "100", currency: "JPY" } }] }] } },
  ]) })
  const page = pageInstance(definition)
  await page.onLoad({ tripId: "trip-1", currency: "CNY" })
  assert.equal(page.data.overview.recordCount, 2)
  assert.equal(page.data.overview.total, "20.00")
  assert.equal(page.data.overview.mixedCurrency, true)
})

test("trip detail shows paid responsibility and pending transfer from settlement preview", async () => {
  let previewPayload
  const definition = loadTripDetail({
    listExpenses: () => Promise.resolve([{
      id: "expense-1", occurred_at: "2026-09-28",
      payload: { participants: ["我", "卢"], expenses: [{ payer_id: "我", settlement_currency: "CNY", items: [{ item_id: "tea", amount: { amount: "100", currency: "CNY" }, allocation: { 我: "0.5", 卢: "0.5" } }] }] },
    }]),
    previewSettlement(tripId, payload) {
      previewPayload = payload
      return Promise.resolve({ groups: [{
        currency: "CNY",
        paid_by_participant: { 我: { amount: "100.00", currency: "CNY" }, 卢: { amount: "0.00", currency: "CNY" } },
        responsibility_by_participant: { 我: { amount: "50.00", currency: "CNY" }, 卢: { amount: "50.00", currency: "CNY" } },
        transfers: [{ from_participant_id: "卢", to_participant_id: "我", amount: { amount: "50.00", currency: "CNY" } }],
      }] })
    },
  })
  const page = pageInstance(definition)

  await page.onLoad({ tripId: "trip-1", currency: "CNY" })

  assert.equal(previewPayload.participants.join(","), "我,卢")
  assert.deepEqual(JSON.parse(JSON.stringify(page.data.settlementGroups)), [{
    currency: "CNY",
    participants: [
      { name: "我", paid: "100.00", responsibility: "50.00" },
      { name: "卢", paid: "0.00", responsibility: "50.00" },
    ],
    transferText: "卢 需付给 我 ¥50.00 CNY",
  }])
  assert.equal(page.data.settlementWarning, "")
})

test("trip detail keeps expenses visible when settlement preview is incomplete", async () => {
  const definition = loadTripDetail({
    listExpenses: () => Promise.resolve([{
      id: "expense-1", occurred_at: "2026-09-28",
      payload: { participants: ["我", "卢"], expenses: [{ payer_id: "我", items: [{ item_id: "tea", amount: { amount: "100", currency: "JPY" }, allocation: { 我: "0.5", 卢: "0.5" } }] }] },
    }]),
    previewSettlement: () => Promise.reject({ message: "missing rate" }),
  })
  const page = pageInstance(definition)

  await page.onLoad({ tripId: "trip-1", currency: "CNY" })

  assert.equal(page.data.expenses.length, 1)
  assert.equal(page.data.settlementGroups.length, 0)
  assert.match(page.data.settlementWarning, /补全.*汇率/)
})

test("trip detail refreshes the overview after returning from a saved expense", async () => {
  let loads = 0
  const definition = loadTripDetail({
    listExpenses() { loads += 1; return Promise.resolve([]) },
  })
  const page = pageInstance(definition)

  await page.onLoad({ tripId: "trip-1", currency: "CNY" })
  await page.onShow()

  assert.equal(loads, 2)
})

test("trip detail opens a local draft after choosing a receipt without creating an expense", async () => {
  let destination = ""
  const app = { globalData: {} }
  const definition = loadTripDetail({
    listExpenses() { return Promise.resolve([]) },
  }, {
    chooseMedia({ success }) { success({ tempFiles: [{ tempFilePath: "wxfile://receipt.jpg" }] }) },
    compressImage({ success }) { success({ tempFilePath: "wxfile://compressed-receipt.jpg" }) },
    navigateTo({ url }) { destination = url },
  }, app)
  const page = pageInstance(definition, { tripId: "trip-1", currency: "CNY" })

  await page.chooseReceiptAndCreate()

  assert.doesNotMatch(destination, /expenseId=/)
  assert.doesNotMatch(destination, /receiptPath=/)
  assert.equal(app.globalData.pendingReceiptPath, "wxfile://compressed-receipt.jpg")
})

test("trip detail exposes compact expense summaries and a receipt-first action", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "..", "pages", "trip-detail", "index.wxml"), "utf8")
  assert.match(wxml, /class="expense-summary"/)
  assert.match(wxml, /class="expense-preview"/)
  assert.match(wxml, /拍小票，记录消费/)
  assert.match(wxml, /旅行结算/)
  assert.match(wxml, /实际支付/)
  assert.match(wxml, /应承担/)
})

test("trip detail prepares a native share path after creating an invite", async () => {
  const definition = loadTripDetail({
    createTripInvite() { return Promise.resolve({ token: "invite-token" }) },
  })
  const page = pageInstance(definition, { tripId: "trip-1" })

  await page.prepareInvite()

  assert.equal(page.data.inviteReady, true)
  assert.equal(page.data.invitePath, "/pages/trip-invite/index?token=invite-token")
  assert.deepEqual(JSON.parse(JSON.stringify(page.onShareAppMessage())), {
    title: "邀请你一起记旅行账",
    path: "/pages/trip-invite/index?token=invite-token",
  })
})

test("owner can delete one expense after confirming", async () => {
  const definition = loadTripDetail({ deleteExpense() { return Promise.resolve() }, listExpenses() { return Promise.resolve([]) } }, { showModal({ success }) { success({ confirm: true }) } })
  const page = pageInstance(definition, { tripId: "trip-1", isOwner: true, expenses: [{ id: "expense-1" }] })

  await page.deleteExpenseRecord({ currentTarget: { dataset: { id: "expense-1" } } })

  assert.deepEqual(JSON.parse(JSON.stringify(page.data.expenses)), [])
})
