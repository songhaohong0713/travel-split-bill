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

test("trip detail creates a dated expense after choosing a receipt", async () => {
  let created
  let destination = ""
  const app = { globalData: {} }
  const definition = loadTripDetail({
    listExpenses() { return Promise.resolve([]) },
    createExpense(_tripId, occurredAt, payload) {
      created = { occurredAt, payload }
      return Promise.resolve({ id: "expense-1" })
    },
  }, {
    chooseMedia({ success }) { success({ tempFiles: [{ tempFilePath: "wxfile://receipt.jpg" }] }) },
    compressImage({ success }) { success({ tempFilePath: "wxfile://compressed-receipt.jpg" }) },
    navigateTo({ url }) { destination = url },
  }, app)
  const page = pageInstance(definition, { tripId: "trip-1", currency: "CNY" })

  await page.chooseReceiptAndCreate()

  assert.match(created.payload.title, /^\d+月\d+日消费$/)
  assert.equal(created.payload.expenses[0].items.length, 0)
  assert.match(destination, /expenseId=expense-1/)
  assert.doesNotMatch(destination, /receiptPath=/)
  assert.equal(app.globalData.pendingReceiptPath, "wxfile://compressed-receipt.jpg")
})

test("trip detail exposes compact expense summaries and a receipt-first action", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "..", "pages", "trip-detail", "index.wxml"), "utf8")
  assert.match(wxml, /class="expense-summary"/)
  assert.match(wxml, /class="expense-preview"/)
  assert.match(wxml, /拍小票，记录消费/)
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
