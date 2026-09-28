# 旅行总览与消费详情 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 旅行账本展示整体账目，已保存消费默认只读，仅主动编辑时显示录入表单。

**Architecture:** 复用 `trip-detail` 作为总览、`expense` 作为详情/编辑双态页面，继续使用现有消费 payload 和结算预览接口。

**Tech Stack:** 微信小程序原生 WXML/WXSS/JavaScript、Node 内置测试。

## Global Constraints

- 不新增页面路由、数据库表、后端 API 或第三方依赖。
- 已保存消费默认只读；无 `expenseId` 的新建消费默认编辑。
- 不可靠的跨币种金额不得合并展示。

---

### Task 1: 已保存消费的只读详情

**Files:**
- Modify: `miniprogram/pages/expense/index.js`
- Modify: `miniprogram/pages/expense/index.wxml`
- Modify: `miniprogram/pages/expense/index.wxss`
- Test: `miniprogram/tests/expense-bill.integration.test.js`

- [ ] Step 1: 先加入红灯测试：带 `expenseId` 加载后 `reading === true`，调用 `beginEditing()` 后为 `false`。
- [ ] Step 2: 运行 `node --test miniprogram/tests/expense-bill.integration.test.js`，确认因缺少该状态和方法失败。
- [ ] Step 3: 在页面数据中加入 `reading`；`onLoad` 对带 `expenseId` 的记录设为只读；新增 `beginEditing()` 切换为编辑。
- [ ] Step 4: WXML 使用 `wx:if` 提供只读分支：标题/日期/总额/付款人、结算信息、紧凑商品行、编辑与删除按钮；保留原表单作为编辑分支。
- [ ] Step 5: 重跑 focused test，确认通过并提交 `feat: show saved expenses in reading mode`。

### Task 2: 旅行总览摘要与消费卡片

**Files:**
- Modify: `miniprogram/pages/trip-detail/index.js`
- Modify: `miniprogram/pages/trip-detail/index.wxml`
- Modify: `miniprogram/pages/trip-detail/index.wxss`
- Test: `miniprogram/tests/trip-detail.integration.test.js`

- [ ] Step 1: 先加入红灯测试：加载两笔同币种消费后，`overview.recordCount === 2` 且总额正确。
- [ ] Step 2: 运行 `node --test miniprogram/tests/trip-detail.integration.test.js`，确认失败。
- [ ] Step 3: 实现 `buildOverview(records, currency)`：只累计旅行币种一致的记录；存在其他币种时设置 `mixedCurrency` 提示。
- [ ] Step 4: 在总览顶部渲染总消费、记录数与多币种提示；消费卡片补齐付款人、商品数、原币金额。
- [ ] Step 5: 重跑 focused test，确认通过并提交 `feat: add travel ledger overview`。

### Task 3: 保存后返回总览与回归

**Files:**
- Modify: `miniprogram/pages/expense/index.js`
- Test: `miniprogram/tests/expense-bill.integration.test.js`

- [ ] Step 1: 先加入红灯测试：保存成功但无法生成结算预览时，调用 `wx.navigateBack()`，而不是弹出保存失败。
- [ ] Step 2: 运行 `node --test miniprogram/tests/expense-bill.integration.test.js`，确认失败。
- [ ] Step 3: 仅在预览数据不完整时返回旅行总览；具备预览时保持现有结算跳转。
- [ ] Step 4: 运行 `node --test miniprogram/tests/*.js`，确认完整回归通过。
- [ ] Step 5: 提交 `fix: return saved entries to travel ledger`。
