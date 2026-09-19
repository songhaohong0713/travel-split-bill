# 旅行存根首页 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将首页改造成可继续最近旅行并可按需新建旅行的旅行存根首页。

**Architecture:** 在小程序 API 客户端增加 `listTrips()`，首页在登录后加载旅行列表并保存加载/错误状态。首页 WXML 用旅行存根列表和可展开的新建旅行区取代默认表单；点击存根和创建成功仍复用现有账单录入页地址。

**Tech Stack:** 微信小程序原生 JavaScript、WXML、WXSS；Node.js 内置测试运行器。

## Global Constraints

- 使用既有 `GET /v1/trips` 与 `POST /v1/trips`，不改后端、数据库或 CloudBase 部署。
- 保持旅行票据簿色彩：`#F4EFE5`、`#FFFDF8`、`#123E45`、`#C86D45`。
- 最近旅行显示名称和默认币种；点击后进入账单录入页。
- 无旅行、加载失败和创建中状态必须给用户明确提示。

---

### Task 1: 提供旅行列表客户端调用

**Files:**
- Modify: `miniprogram/services/api.js`
- Create: `miniprogram/tests/home.integration.test.js`

**Interfaces:**
- Produces: `listTrips(): Promise<Array<{id: string, name: string, default_currency: string}>>`。
- Consumes: 既有 `request(path, options)`。

- [ ] **Step 1: 写出失败测试**

```js
test("listTrips requests the current user's trips", async () => {
  const calls = []
  const api = loadApi(calls, [{ id: "trip-1", name: "东京周末", default_currency: "JPY" }])
  const trips = await api.listTrips()
  assert.equal(calls[0].url, "https://example.test/v1/trips")
  assert.equal(calls[0].method, "GET")
  assert.equal(trips[0].name, "东京周末")
})
```

- [ ] **Step 2: 验证测试失败**

Run: `node --test miniprogram/tests/home.integration.test.js`

Expected: FAIL，因为 `listTrips` 尚未导出。

- [ ] **Step 3: 实现最小客户端函数**

```js
function listTrips() {
  return request("/v1/trips")
}
```

Add `listTrips` to the API module export.

- [ ] **Step 4: 验证测试通过**

Run: `node --test miniprogram/tests/home.integration.test.js`

Expected: PASS.

- [ ] **Step 5: 提交**

```bash
git add miniprogram/services/api.js miniprogram/tests/home.integration.test.js
git commit -m "Add trips list client"
```

### Task 2: 加载与打开最近旅行

**Files:**
- Modify: `miniprogram/pages/trips/index.js`
- Modify: `miniprogram/tests/home.integration.test.js`

**Interfaces:**
- Consumes: `login()`, `listTrips()` 与小程序 `wx.navigateTo`。
- Produces: `loadTrips()`、`openTrip(event)`；页面数据 `trips`、`loadingTrips`、`loadError`。

- [ ] **Step 1: 写出失败测试**

```js
test("home loads trips after login and opens a selected trip", async () => {
  const { definition } = loadHomePage({
    login: () => Promise.resolve(),
    listTrips: () => Promise.resolve([{ id: "trip-1", name: "东京周末", default_currency: "JPY" }]),
    createTrip() {},
  }, calls)
  const page = pageInstance(definition)
  await page.onLoad()
  assert.equal(page.data.trips[0].name, "东京周末")
  page.openTrip({ currentTarget: { dataset: { id: "trip-1", currency: "JPY" } } })
  assert.match(calls[0].url, /tripId=trip-1/)
  assert.match(calls[0].url, /currency=JPY/)
})
```

- [ ] **Step 2: 验证测试失败**

Run: `node --test miniprogram/tests/home.integration.test.js`

Expected: FAIL，因为首页尚无旅行列表状态和 `openTrip`。

- [ ] **Step 3: 实现最小页面状态与行为**

```js
data: { trips: [], loadingTrips: true, loadError: "", showCreateForm: false, creating: false, ... }

onLoad() {
  return login().then(() => this.loadTrips()).catch(() => this.setData({ loadingTrips: false, loadError: "登录未完成" }))
}

loadTrips() {
  return listTrips().then((trips) => this.setData({ trips, loadingTrips: false, loadError: "" }))
}

openTrip(event) {
  const { id, currency } = event.currentTarget.dataset
  wx.navigateTo({ url: `/pages/expense/index?tripId=${encodeURIComponent(id)}&currency=${encodeURIComponent(currency)}` })
}
```

Ensure `createTrip` sets and clears `creating`, and only shows a user-facing failure message when list loading fails.

- [ ] **Step 4: 验证测试通过**

Run: `node --test miniprogram/tests/home.integration.test.js`

Expected: PASS.

- [ ] **Step 5: 提交**

```bash
git add miniprogram/pages/trips/index.js miniprogram/tests/home.integration.test.js
git commit -m "Load recent trips on home page"
```

### Task 3: 实现旅行存根首页布局

**Files:**
- Modify: `miniprogram/pages/trips/index.wxml`
- Modify: `miniprogram/pages/trips/index.wxss`
- Modify: `miniprogram/tests/home.integration.test.js`

**Interfaces:**
- Consumes: `trips`、`loadingTrips`、`loadError`、`showCreateForm`、`creating`、`openTrip`、`createTrip`。
- Produces: 旅行存根、空状态、可展开的新建旅行表单和布局测试钩子。

- [ ] **Step 1: 写出失败测试**

```js
test("home exposes travel-stub layout hooks", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "..", "pages", "trips", "index.wxml"), "utf8")
  assert.match(wxml, /class="travel-stub"/)
  assert.match(wxml, /class="empty-trips"/)
  assert.match(wxml, /class="create-panel"/)
})
```

- [ ] **Step 2: 验证测试失败**

Run: `node --test miniprogram/tests/home.integration.test.js`

Expected: FAIL，因为旅行存根布局尚未存在。

- [ ] **Step 3: 实现 WXML 与 WXSS**

```xml
<view class="page trips-page">
  <view class="home-hero">...</view>
  <view wx:if="{{loadingTrips}}" class="loading-trips">正在整理旅行账本…</view>
  <view wx:elif="{{trips.length}}" class="trip-list">...</view>
  <view wx:else class="empty-trips">...</view>
  <view class="create-panel">...</view>
</view>
```

Render every trip as a button-like `travel-stub` with `data-id` and `data-currency`. Keep the creation form hidden until the user triggers `toggleCreateForm`; it is always visible for the empty state.

- [ ] **Step 4: 验证测试通过**

Run: `node --test miniprogram/tests/home.integration.test.js`

Expected: PASS.

- [ ] **Step 5: 全量验证并提交**

```bash
node --test miniprogram/tests/*.test.js
git diff --check
git add miniprogram/pages/trips/index.wxml miniprogram/pages/trips/index.wxss miniprogram/tests/home.integration.test.js
git commit -m "Redesign home as travel stubs"
```

Expected: all Node tests pass and Git reports no whitespace errors.
