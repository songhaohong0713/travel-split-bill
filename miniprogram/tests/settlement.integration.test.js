const assert = require("node:assert/strict")
const fs = require("node:fs")
const path = require("node:path")
const test = require("node:test")
const vm = require("node:vm")

function loadPage(api) {
  const source = fs.readFileSync(path.join(__dirname, "..", "pages", "settlement", "index.js"), "utf8")
  let definition
  const module = { exports: {} }
  vm.runInNewContext(source, { Page(value) { definition = value }, require(request) { return request.includes("member-labels") ? require("../services/member-labels") : api }, getApp() { return { globalData: {} } }, wx: {}, module, exports: module.exports, Promise })
  return { definition, helpers: module.exports }
}

test("settlement maps stable ids to me and a nickname without changing ids", async () => {
  const api = { listTripMembers: () => Promise.resolve([
    { id: "owner-secret-id", is_current: true },
    { id: "peer-secret-id", is_current: false, nickname: "小卢" },
  ]) }
  const { definition } = loadPage(api)
  const page = { ...definition, data: { ...definition.data }, setData(value) { this.data = { ...this.data, ...value } } }
  const result = { transfers: [{ from_participant_id: "peer-secret-id", to_participant_id: "owner-secret-id", amount: { amount: "1000", currency: "JPY" } }] }

  await page.onLoad({ tripId: "trip-1", result: encodeURIComponent(JSON.stringify(result)), preview: "" })

  const transfer = page.data.currencyGroups[0].transfers[0]
  assert.equal(transfer.fromName, "小卢")
  assert.equal(transfer.toName, "我")
  assert.equal(transfer.from_participant_id, "peer-secret-id")
})

test("settlement falls back to a friendly companion label", () => {
  const { helpers } = loadPage({})
  const groups = helpers.displayGroups({ transfers: [{ from_participant_id: "hidden-a", to_participant_id: "hidden-b", amount: { amount: "10", currency: "CNY" } }] }, { "hidden-a": "我" })
  assert.equal(groups[0].transfers[0].toName, "同行人")
})
