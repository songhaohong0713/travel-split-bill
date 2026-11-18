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
    require(name) { return {isLocalTrip:()=>false} },
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
