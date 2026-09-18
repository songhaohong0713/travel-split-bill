# 旅行票据簿界面改版 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将账单录入页与结算页改造成可核对、移动端友好的旅行票据簿界面，同时不改变现有保存、预览、发布和分享 API 行为。

**Architecture:** 保持现有 `Page` 数据结构与 API 调用不变，只增加可呈现的派生状态（选中条目数、账单总额）和对应测试。WXML 重新组织为摘要、票据条目、批量规则、账单调整和固定操作栏；WXSS 以统一令牌定义纸张、票据与操作层级。

**Tech Stack:** 微信小程序原生 WXML/WXSS/JavaScript，Node.js 内置测试运行器。

## Global Constraints

- 使用墨绿 `#123E45`、米色 `#F4EFE5`、票据白 `#FFFDF8`、橘棕 `#C86D45`、浅绿 `#DCEBE3`。
- 保持双人分账、商品批量选择、税费/优惠、OCR 入口、结算发布与分享的当前功能。
- 页面保持单列并适配窄屏；金额右对齐且不折行。
- 不改变服务端 API、数据库模型或 OCR 服务端链路。

---

### Task 1: 为账单摘要补充可测试的派生数据

**Files:**
- Modify: `miniprogram/pages/expense/index.js`
- Modify: `miniprogram/tests/expense-bill.integration.test.js`

**Interfaces:**
- Consumes: `items: Array<{selected: boolean, amount: string}>` 与现有 `cents(value)`。
- Produces: `selectedItemCount(items): number` 与 `billTotal(items, taxAmount, taxIncluded, adjustmentAmount): string`，用于录入页展示。

- [ ] **Step 1: 写出失败测试**

```js
test("bill summary counts selected items and includes adjustments", () => {
  const { helpers } = loadExpensePage({})
  const items = [
    { selected: true, amount: "20" },
    { selected: false, amount: "10" },
  ]
  assert.equal(helpers.selectedItemCount(items), 1)
  assert.equal(helpers.billTotal(items, "2", false, "-3"), "29.00")
})
```

- [ ] **Step 2: 验证测试失败**

Run: `node --test miniprogram/tests/expense-bill.integration.test.js`

Expected: FAIL，因为 `selectedItemCount` 与 `billTotal` 尚未导出。

- [ ] **Step 3: 实现最小派生函数**

```js
function selectedItemCount(items) {
  return (items || []).filter((item) => item.selected).length
}

function billTotal(items, taxAmount, taxIncluded, adjustmentAmount) {
  const itemTotal = (items || []).reduce((total, item) => total + (cents(item.amount) || 0), 0)
  const tax = taxIncluded ? 0 : (cents(taxAmount) || 0)
  return money(itemTotal + tax + (cents(adjustmentAmount) || 0))
}
```

Expose the two functions through the existing CommonJS test export.

- [ ] **Step 4: 验证测试通过**

Run: `node --test miniprogram/tests/expense-bill.integration.test.js`

Expected: PASS.

- [ ] **Step 5: 提交**

```bash
git add miniprogram/pages/expense/index.js miniprogram/tests/expense-bill.integration.test.js
git commit -m "Add bill summary display helpers"
```

### Task 2: 重组录入页为旅行票据簿

**Files:**
- Modify: `miniprogram/pages/expense/index.wxml`
- Modify: `miniprogram/pages/expense/index.wxss`

**Interfaces:**
- Consumes: 现有 `items`、`payer`、`friend`、`batchModes`、`saving` 与 Task 1 的 `selectedItemCount`/`billTotal`。
- Produces: 明确的摘要、票据条目、批量规则、调整和底部总计布局；保留所有现有事件绑定。

- [ ] **Step 1: 写出失败测试**

```js
test("expense page exposes receipt-book layout hooks", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "..", "pages", "expense", "index.wxml"), "utf8")
  assert.match(wxml, /class="bill-summary"/)
  assert.match(wxml, /class="batch-bar"/)
  assert.match(wxml, /class="checkout-bar"/)
})
```

- [ ] **Step 2: 验证测试失败**

Run: `node --test miniprogram/tests/expense-bill.integration.test.js`

Expected: FAIL，因为新版布局钩子尚未存在。

- [ ] **Step 3: 实现新版 WXML 与 WXSS**

Use the following structural skeleton, retaining all existing `bind*` handlers and form fields:

```xml
<view class="page expense-page">
  <view class="bill-summary">...</view>
  <view class="receipt-section">...</view>
  <view class="ticket-list">...</view>
  <view class="batch-bar">...</view>
  <view class="adjustment-section">...</view>
  <view class="checkout-bar">...</view>
</view>
```

Define the palette as reusable WXSS classes, give selected ticket cards the `#DCEBE3` treatment, and reserve `#C86D45` for monetary total and warnings. The checkout bar must visually persist at the bottom without obscuring the final form field.

- [ ] **Step 4: 验证测试通过**

Run: `node --test miniprogram/tests/expense-bill.integration.test.js`

Expected: PASS.

- [ ] **Step 5: 提交**

```bash
git add miniprogram/pages/expense/index.wxml miniprogram/pages/expense/index.wxss miniprogram/tests/expense-bill.integration.test.js
git commit -m "Redesign expense entry as travel receipt book"
```

### Task 3: 重组结算页为可核对的结算单

**Files:**
- Modify: `miniprogram/pages/settlement/index.wxml`
- Modify: `miniprogram/pages/settlement/index.wxss`
- Modify: `miniprogram/tests/expense-bill.integration.test.js`

**Interfaces:**
- Consumes: `transfers`、`published`、`publishing`、`sharing`、`shareUrl` 与既有事件 `publish`、`share`、`copyShareLink`。
- Produces: 结算单头部、明确转账卡、已结清空态和次级分享区。

- [ ] **Step 1: 写出失败测试**

```js
test("settlement page exposes settlement-sheet layout hooks", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "..", "pages", "settlement", "index.wxml"), "utf8")
  assert.match(wxml, /class="settlement-sheet"/)
  assert.match(wxml, /class="settlement-hero"/)
  assert.match(wxml, /已经结清/)
})
```

- [ ] **Step 2: 验证测试失败**

Run: `node --test miniprogram/tests/expense-bill.integration.test.js`

Expected: FAIL，因为结算单布局钩子尚未存在。

- [ ] **Step 3: 实现结算单 WXML 与 WXSS**

```xml
<view class="page settlement-sheet">
  <view class="settlement-hero">...</view>
  <view wx:if="{{transfers.length}}" class="transfer-list">...</view>
  <view wx:else class="settled-state">已经结清...</view>
  <view class="share-section">...</view>
</view>
```

Display transfer amount in a dedicated, right-aligned amount element. Keep publish as the primary action and sharing as a follow-up region after a successful publish.

- [ ] **Step 4: 验证测试通过**

Run: `node --test miniprogram/tests/expense-bill.integration.test.js`

Expected: PASS.

- [ ] **Step 5: 完整验证并提交**

```bash
node --test miniprogram/tests/*.test.js
git diff --check
git add miniprogram/pages/settlement/index.wxml miniprogram/pages/settlement/index.wxss miniprogram/tests/expense-bill.integration.test.js
git commit -m "Redesign settlement as receipt sheet"
```

Expected: all Node tests pass and Git reports no whitespace errors.
