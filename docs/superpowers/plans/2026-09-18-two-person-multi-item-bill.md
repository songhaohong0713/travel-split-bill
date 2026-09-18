# 双人多商品账单 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 支持双人多商品账单、批量设置分账，并在结算前永久保存消费记录。

**Architecture:** 后端已有消费记录创建和结算预览接口。本次只补小程序的多条商品状态、批量分配和保存调用；保存的完整结算 payload 直接传给既有预览 API，不新增数据库表或依赖。

**Tech Stack:** 微信小程序原生 JavaScript/WXML/WXSS、FastAPI 既有 API、Node 内置测试、pytest。

## Global Constraints

- 仅支持付款人和同行人两人。
- 每次创建消费记录都带 UUID 格式 `Idempotency-Key`。
- 保存失败不清空页面。
- 仅在保存成功后跳转结算预览。

---

### Task 1: 消费记录 API 封装

**Files:**
- Modify: `miniprogram/services/api.js`
- Create: `miniprogram/tests/expense-bill.integration.test.js`

**Interfaces:** `createExpense(tripId, occurredAt, payload)` 调用 `POST /v1/trips/{tripId}/expenses`，body 为 `{ occurred_at, payload }`，header 为 UUID `Idempotency-Key`。

- [ ] **Step 1: 写失败测试**

```js
test("createExpense adds a UUID idempotency key", async () => {
  const calls = []
  const api = loadApi({ request: (path, options) => { calls.push({ path, options }); return Promise.resolve({}) } })
  await api.createExpense("trip-1", "2026-09-18", { items: [] })
  assert.equal(calls[0].path, "/v1/trips/trip-1/expenses")
  assert.match(calls[0].options.header["Idempotency-Key"], /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i)
})
```

- [ ] **Step 2: 验证失败**

Run: `node --test miniprogram/tests/expense-bill.integration.test.js`  
Expected: FAIL，因为 `createExpense` 尚未导出。

- [ ] **Step 3: 最小实现**

在 `api.js` 添加 `uuid4()`（模板 `xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx`，用 `Math.random` 替换）及：

```js
function createExpense(tripId, occurredAt, payload) {
  return request(`/v1/trips/${tripId}/expenses`, {
    method: "POST", data: { occurred_at: occurredAt, payload },
    header: { "Idempotency-Key": uuid4() },
  })
}
```

将其加入 `module.exports`。

- [ ] **Step 4: 验证通过并提交**

Run: `node --test miniprogram/tests/expense-bill.integration.test.js`  
Expected: PASS。

```bash
git add miniprogram/services/api.js miniprogram/tests/expense-bill.integration.test.js
git commit -m "Add expense persistence API"
```

### Task 2: 多商品与批量分配

**Files:**
- Modify: `miniprogram/pages/expense/index.js`
- Modify: `miniprogram/pages/expense/index.wxml`
- Modify: `miniprogram/pages/expense/index.wxss`
- Modify: `miniprogram/tests/expense-bill.integration.test.js`

**Interfaces:** 页面使用 `items: [{ id, name, amount, selected, allocationMode, payerPercent, friendPercent }]`。导出纯函数 `allocationFor(item, payer, friend)`、`validateBill(data)` 与 `buildBillPayload(data)`。

- [ ] **Step 1: 写失败测试**

```js
test("buildBillPayload keeps per-item allocations", () => {
  const result = bill.buildBillPayload({ currency: "CNY", payer: "我", friend: "小王", items: [
    { id: "a", name: "晚餐", amount: "20", allocationMode: "split", payerPercent: "50", friendPercent: "50" },
    { id: "b", name: "甜点", amount: "10", allocationMode: "friend", payerPercent: "0", friendPercent: "100" },
  ], taxAmount: "", adjustmentAmount: "", adjustmentType: 0 })
  assert.deepEqual(JSON.parse(JSON.stringify(result.expenses[0].items.map((item) => item.allocation))), [{ "我": "0.5", "小王": "0.5" }, { "小王": "1" }])
})
test("validateBill rejects custom shares that are not 100 percent", () => {
  assert.match(bill.validateBill({ payer: "我", friend: "小王", items: [{ name: "餐费", amount: "20", allocationMode: "custom", payerPercent: "70", friendPercent: "20" }] }), /100%/)
})
```

- [ ] **Step 2: 验证失败**

Run: `node --test miniprogram/tests/expense-bill.integration.test.js`  
Expected: FAIL，因为纯函数尚不存在。

- [ ] **Step 3: 最小实现**

```js
function allocationFor(item, payer, friend) {
  if (item.allocationMode === "payer") return { [payer]: "1" }
  if (item.allocationMode === "friend") return { [friend]: "1" }
  if (item.allocationMode === "split") return { [payer]: "0.5", [friend]: "0.5" }
  return { [payer]: String(Number(item.payerPercent) / 100), [friend]: String(Number(item.friendPercent) / 100) }
}
```

初始商品列表保留一条空项；增加 `addItem`、`removeItem`、`toggleItem`、`applyBatchAllocation`。批量操作仅修改已勾选项，未勾选时提示“请先选择商品”。`validateBill` 检查两人名称、商品名称、正金额、两人不同及自定义比例总和 100%。`buildBillPayload` 输出一条 expense 和多条 item；税费、优惠/退款延续既有字段。用整数分汇总实际支付金额，避免浮点误差。

将 WXML 单商品输入替换为商品列表（勾选、名称、金额、分配 picker）；添加“添加商品”及批量规则控件；自定义规则显示付款人和同行人百分比输入。

- [ ] **Step 4: 验证通过并提交**

Run: `node --test miniprogram/tests/expense-bill.integration.test.js miniprogram/tests/ocr-review.integration.test.js`  
Expected: PASS。

```bash
git add miniprogram/pages/expense/index.js miniprogram/pages/expense/index.wxml miniprogram/pages/expense/index.wxss miniprogram/tests/expense-bill.integration.test.js
git commit -m "Support multi-item bill allocation"
```

### Task 3: 先保存再预览

**Files:**
- Modify: `miniprogram/pages/expense/index.js`
- Modify: `miniprogram/pages/expense/index.wxml`
- Modify: `miniprogram/tests/expense-bill.integration.test.js`
- Test: `backend/tests/integration/test_settlement_preview.py`

**Interfaces:** `saveAndPreview()` 先调用 `createExpense`，成功后调用 `previewSettlement`，最后进入结算页。

- [ ] **Step 1: 写失败测试**

```js
test("saveAndPreview persists the bill before previewing", async () => {
  const calls = []
  const page = loadExpensePage({
    createExpense() { calls.push("save"); return Promise.resolve({ id: "expense-1" }) },
    previewSettlement() { calls.push("preview"); return Promise.resolve({ transfers: [] }) },
    uploadReceipt() {}, getReceiptJob() {},
  }, { navigateTo() {} })
  const instance = pageInstance(page, validTwoPersonBill)
  await instance.saveAndPreview()
  assert.deepEqual(calls, ["save", "preview"])
})
```

- [ ] **Step 2: 验证失败**

Run: `node --test miniprogram/tests/expense-bill.integration.test.js`  
Expected: FAIL，因为 `saveAndPreview` 尚不存在。

- [ ] **Step 3: 最小实现**

`saveAndPreview` 先执行 `validateBill`；有效时构造一次 `previewPayload`，依序调用：

```js
createExpense(tripId, new Date().toISOString().slice(0, 10), previewPayload)
  .then(() => previewSettlement(tripId, previewPayload))
  .then((result) => wx.navigateTo({ url: settlementUrl(tripId, previewPayload, result) }))
```

失败时保留页面数据并显示 `error.message || "保存账单失败"`。按钮改为“保存账单并查看分账预览”，用 `saving` loading 状态防止重复提交。

- [ ] **Step 4: 回归并提交**

Run: `node --test miniprogram/tests/*.test.js`  
Expected: PASS。

Run: `uv run pytest backend/tests/integration/test_settlement_preview.py -q`  
Expected: PASS。

```bash
git add miniprogram/pages/expense/index.js miniprogram/pages/expense/index.wxml miniprogram/tests/expense-bill.integration.test.js
git commit -m "Persist bills before settlement preview"
```

### Task 4: 全量验证与手工验收

- [ ] **Step 1: 运行完整检查**

Run: `node --test miniprogram/tests/*.test.js`  
Expected: PASS。

Run: `uv run pytest backend/tests -q`  
Expected: PASS。

Run: `uv run ruff check backend; uv run mypy backend/app`  
Expected: 两项均成功。

- [ ] **Step 2: 微信开发者工具验收**

添加三条商品；批量选前两条并两人均分；第三条改为同行人 100% 承担；保存并进入预览；确认有转账建议；重开行程后确认消费记录仍存在。

- [ ] **Step 3: 最终检查**

Run: `git status --short`  
Expected: 仅本计划文件被提交，保留用户既有未提交改动。
