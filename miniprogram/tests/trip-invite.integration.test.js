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
    wx: { getStorageSync() { return "" }, request(options) { calls.push(options); options.success({ statusCode: 200, data: { data: { id: "trip-1" } } }) } },
    module, exports: module.exports, Promise,
  })
  return module.exports
}

test("acceptTripInvite posts to the one-time invite endpoint", async () => {
  const calls = []
  const api = loadApi(calls)
  await api.acceptTripInvite("invite-token")
  assert.equal(calls[0].url, "https://example.test/v1/trip-invites/invite-token/accept")
  assert.equal(calls[0].method, "POST")
})

test("createTripInvite requests a server-generated invite", async () => {
  const calls = []
  const api = loadApi(calls)
  await api.createTripInvite("trip-1")
  assert.equal(calls[0].url, "https://example.test/v1/trips/trip-1/invites")
  assert.equal(calls[0].method, "POST")
})
test("trip invite page is registered", () => {
  const config = fs.readFileSync(path.join(__dirname, "..", "app.json"), "utf8")
  assert.match(config, /pages\/trip-invite\/index/)
})
