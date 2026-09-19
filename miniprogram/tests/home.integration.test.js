const assert = require("node:assert/strict")
const fs = require("node:fs")
const path = require("node:path")
const test = require("node:test")
const vm = require("node:vm")

function loadApi(calls, payload) {
  const source = fs.readFileSync(path.join(__dirname, "..", "services", "api.js"), "utf8")
  const module = { exports: {} }
  vm.runInNewContext(source, {
    getApp() { return { globalData: { apiBaseUrl: "https://example.test" } } },
    wx: {
      getStorageSync() { return "" },
      request(options) {
        calls.push(options)
        options.success({ statusCode: 200, data: { data: payload } })
      },
    },
    module,
    exports: module.exports,
    Promise,
  })
  return module.exports
}

test("listTrips requests the current user's trips", async () => {
  const calls = []
  const api = loadApi(calls, [{ id: "trip-1", name: "东京周末", default_currency: "JPY" }])

  const trips = await api.listTrips()

  assert.equal(calls[0].url, "https://example.test/v1/trips")
  assert.equal(calls[0].method, "GET")
  assert.equal(trips[0].name, "东京周末")
})
