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

function loadHomePage(api, wx = {}) {
  const source = fs.readFileSync(path.join(__dirname, "..", "pages", "trips", "index.js"), "utf8")
  let definition
  vm.runInNewContext(source, {
    Page(value) { definition = value },
    require() { return api },
    wx: { showToast() {}, ...wx },
    Promise,
    encodeURIComponent,
  })
  return { definition }
}

function pageInstance(definition, data = {}) {
  return { ...definition, data: { ...definition.data, ...data }, setData(value) { this.data = { ...this.data, ...value } } }
}

test("home loads trips after login and opens a selected trip", async () => {
  const calls = []
  const { definition } = loadHomePage({
    login: () => Promise.resolve(),
    listTrips: () => Promise.resolve([{ id: "trip-1", name: "东京周末", default_currency: "JPY" }]),
    createTrip() {},
  }, { navigateTo(value) { calls.push(value) } })
  const page = pageInstance(definition)

  await page.onLoad()

  assert.equal(page.data.trips[0].name, "东京周末")
  page.openTrip({ currentTarget: { dataset: { id: "trip-1", currency: "JPY" } } })
  assert.match(calls[0].url, /tripId=trip-1/)
  assert.match(calls[0].url, /currency=JPY/)
})
