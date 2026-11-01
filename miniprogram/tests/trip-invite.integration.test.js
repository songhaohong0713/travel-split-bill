const assert = require("node:assert/strict")
const fs = require("node:fs")
const path = require("node:path")
const test = require("node:test")
const vm = require("node:vm")

function loadApi(calls, wxOverrides = {}) {
  const source = fs.readFileSync(path.join(__dirname, "..", "services", "api.js"), "utf8")
  const module = { exports: {} }
  vm.runInNewContext(source, {
    getApp() { return { globalData: { apiBaseUrl: "https://example.test" } } },
    wx: { getStorageSync() { return "token" }, request(options) { calls.push(options); options.success({ statusCode: 200, data: { data: { id: "trip-1", already_member:false } } }) }, ...wxOverrides },
    require(name) { return {isLocalTrip:()=>false} },
    module, exports: module.exports, Promise,
  })
  return module.exports
}

function loadInvitePage(api, wx = {}) {
  const source = fs.readFileSync(path.join(__dirname, "..", "pages", "trip-invite", "index.js"), "utf8")
  let definition
  vm.runInNewContext(source, { Page(value) { definition = value }, require() { return api }, wx, Promise, module: { exports: {} } })
  return { ...definition, data: { ...definition.data }, setData(value) { this.data = { ...this.data, ...value } } }
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

test("a cold invite login is shared by preview and accept", async () => {
  const calls = []
  const storage = {}
  let loginCount = 0
  const api = loadApi(calls, {
    getStorageSync(key) { return storage[key] || "" },
    setStorageSync(key, value) { storage[key] = value },
    login({ success }) { loginCount += 1; success({ code: "wechat-code" }) },
    request(options) {
      calls.push(options)
      if (options.url.endsWith("/v1/auth/wechat")) return options.success({ statusCode: 200, data: { data: { access_token: "fresh-access", refresh_token: "fresh-refresh" } } })
      options.success({ statusCode: 200, data: { data: { id: "trip-1", already_member:false } } })
    },
  })

  await Promise.all([api.ensureAuthenticated(), api.ensureAuthenticated()])
  await api.getTripInvite("invite-token")
  await api.acceptTripInvite("invite-token")

  assert.equal(loginCount, 1)
  assert.equal(calls.filter((call) => call.url.endsWith("/accept")).length, 1)
  assert.equal(calls.at(-1).header.Authorization, "Bearer fresh-access")
})

test("an expired access token refreshes and retries a protected request once", async () => {
  const calls = []
  const storage = { access_token: "expired", refresh_token: "refresh-me" }
  const api = loadApi(calls, {
    getStorageSync(key) { return storage[key] || "" },
    setStorageSync(key, value) { storage[key] = value },
    request(options) {
      calls.push(options)
      if (options.url.endsWith("/v1/auth/refresh")) return options.success({ statusCode: 200, data: { data: { access_token: "fresh", refresh_token: "rotated" } } })
      if (options.header.Authorization === "Bearer expired") return options.success({ statusCode: 401, data: { error: { code: "HTTP_ERROR", message: "invalid access token" } } })
      options.success({ statusCode: 200, data: { data: { id: "trip-1", already_member:false } } })
    },
  })

  await api.acceptTripInvite("invite-token")

  assert.equal(calls.filter((call) => call.url.endsWith("/accept")).length, 2)
  assert.equal(calls.filter((call) => call.url.endsWith("/v1/auth/refresh")).length, 1)
  assert.equal(storage.access_token, "fresh")
})

test("invite page preserves its token when cold-start login fails", async () => {
  let previewCalls = 0
  const page = loadInvitePage({
    ensureAuthenticated: () => Promise.reject({ message: "网络不可用" }),
    getTripInvite: () => { previewCalls += 1; return Promise.resolve({}) },
    acceptTripInvite: () => Promise.resolve(),
  })

  await page.onLoad({ token: "keep-this-invite" })

  assert.equal(page.data.token, "keep-this-invite")
  assert.equal(previewCalls, 0)
  assert.match(page.data.error, /网络不可用/)
})

test("invite page continues loading the original invite after cold-start login", async () => {
  const calls = []
  const page = loadInvitePage({
    ensureAuthenticated: () => { calls.push("login"); return Promise.resolve() },
    getTripInvite: (token) => { calls.push(`invite:${token}`); return Promise.resolve({ name: "东京旅行" }) },
    acceptTripInvite: () => Promise.resolve(),
  })

  await page.onLoad({ token: "original-token" })

  assert.deepEqual(calls, ["login", "invite:original-token"])
  assert.equal(page.data.invite.name, "东京旅行")
  assert.equal(page.data.loading, false)
})

test('legacy invitation checks real membership before offering join',async()=>{
 const calls=[];const api=loadApi(calls,{request(o){calls.push(o);o.success({statusCode:200,data:{data:o.url.endsWith('/v1/trips')?[{id:'own'}]:{trip_id:'own',name:'东京'}}})}});
 assert.equal((await api.getTripInvite('mine')).already_member,true);
 assert.equal(calls.filter(c=>c.method==='POST').length,0);
});

test('v2 expenses cannot be silently calculated by a legacy backend',async()=>{
 const calls=[];const api=loadApi(calls,{request(o){calls.push(o);o.success(o.url.endsWith('/v1/capabilities')?{statusCode:404,data:{error:{message:'Not Found'}}}:{statusCode:201,data:{data:{id:'should-not-create'}}})}});
 await assert.rejects(api.createExpense('t','2026-10-28',{expenses:[{calculation_version:'v2'}]}),e=>/更新/.test(e.message));
 assert.equal(calls.filter(o=>o.method==='POST').length,0);
});
