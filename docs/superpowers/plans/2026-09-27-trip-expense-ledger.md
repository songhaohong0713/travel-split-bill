# 旅行消费记录与紧凑条目 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let each trip contain multiple named consumption records, each containing a compact editable item list, and settle the complete trip.

**Architecture:** Reuse the existing `expenses` API and JSON payload: a new trip-detail page reads the existing list endpoint, and the existing expense page becomes a single-record editor addressed by `tripId` and optional `expenseId`. The client reads all expenses when constructing a settlement preview; the backend schema and RPCs remain unchanged.

**Tech Stack:** WeChat Mini Program native pages/WXML/WXSS, existing FastAPI REST endpoints, Node built-in test runner.

## Global Constraints

- Support exactly two trip members; both may edit every expense.
- Do not create database tables, migrations, dependencies, or permission roles.
- Generate the default expense title as `<M>月<D>日消费`; users may edit it.
- Keep CloudBase API keys and user-specific DevTools files out of commits.
- Use native WeChat sharing; do not represent a mini-program route string as a shareable external URL.

---

## File structure

- `miniprogram/pages/trip-detail/`: new trip-level consumption list and invitation entry.
- `miniprogram/pages/expense/`: single consumption editor, persistence and compact item disclosure.
- `miniprogram/pages/settlement/`: unchanged presentation, receives a preview assembled from all expense records.
- `miniprogram/services/api.js`: list and revision-aware update wrappers for existing APIs.
- `miniprogram/tests/`: focused page and pure-helper tests; no new framework.

### Task 1: Expose existing expense read/update APIs to the mini program

**Files:**
- Modify: `miniprogram/services/api.js`
- Modify: `miniprogram/tests/expense-bill.integration.test.js`

**Interfaces:**
- Produces `listExpenses(tripId): Promise<ExpenseRecord[]>`.
- Produces `updateExpense(tripId, expenseId, revision, occurredAt, payload): Promise<ExpenseRecord>`.
- `ExpenseRecord` is `{ id, revision, occurred_at, payload }`.

- [ ] **Step 1: Write the failing API-wrapper assertions**

Add a `wx.request` spy and assert the wrappers issue:

```js
assert.equal(calls[0].url.endsWith("/v1/trips/trip-1/expenses"), true)
assert.equal(calls[0].method, "GET")
assert.equal(calls[1].url.endsWith("/v1/trips/trip-1/expenses/expense-1"), true)
assert.equal(calls[1].method, "PATCH")
assert.deepEqual(calls[1].data, { revision: 2, occurred_at: "2026-09-27", payload: { title: "晚餐" } })
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `node --test miniprogram/tests/expense-bill.integration.test.js`  
Expected: FAIL because the exported wrappers do not exist.

- [ ] **Step 3: Add the smallest service wrappers**

```js
function listExpenses(tripId) {
  return request(`/v1/trips/${tripId}/expenses`)
}

function updateExpense(tripId, expenseId, revision, occurredAt, payload) {
  return request(`/v1/trips/${tripId}/expenses/${expenseId}`, {
    method: "PATCH",
    data: { revision, occurred_at: occurredAt, payload },
  })
}
```

Export both functions with the existing API helpers.

- [ ] **Step 4: Run the focused test to verify it passes**

Run: `node --test miniprogram/tests/expense-bill.integration.test.js`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add miniprogram/services/api.js miniprogram/tests/expense-bill.integration.test.js
git commit -m "Expose trip expense APIs to mini program"
```

### Task 2: Add a trip-level consumption list

**Files:**
- Create: `miniprogram/pages/trip-detail/index.js`
- Create: `miniprogram/pages/trip-detail/index.wxml`
- Create: `miniprogram/pages/trip-detail/index.wxss`
- Create: `miniprogram/pages/trip-detail/index.json`
- Modify: `miniprogram/app.json`
- Modify: `miniprogram/pages/trips/index.js`
- Modify: `miniprogram/tests/home.integration.test.js`
- Create: `miniprogram/tests/trip-detail.integration.test.js`

**Interfaces:**
- Consumes `listExpenses`, `createExpense`, `createTripInvite`.
- Opens the editor at `/pages/expense/index?tripId=<id>&currency=<currency>&expenseId=<id>`.
- A new expense payload is one valid empty bill with `{ title, participants: ["我", ""], settlement_currency, expenses: [{ expense_id, payer_id: "我", items: [], adjustments: [] }] }`.

- [ ] **Step 1: Write the failing list-page tests**

Cover load, empty state, list mapping and new-record navigation:

```js
assert.equal(page.data.expenses[0].title, "第一晚晚餐")
assert.equal(page.data.expenses[0].itemCount, 2)
assert.match(navigatedUrl, /expenseId=expense-1/)
assert.match(created.payload.title, /月\d+日消费/)
```

- [ ] **Step 2: Run the new test to verify it fails**

Run: `node --test miniprogram/tests/trip-detail.integration.test.js`  
Expected: FAIL because the page module does not exist.

- [ ] **Step 3: Build the list page with native primitives**

Implement `onLoad`, `loadExpenses`, `createExpenseRecord`, and `openExpense`. Derive each list row from the existing payload:

```js
function expenseSummary(record, currency) {
  const bill = record.payload.expenses && record.payload.expenses[0]
  const items = (bill && bill.items) || []
  const cents = items.reduce((sum, item) => sum + Math.round(Number(item.amount.amount) * 100), 0)
  return {
    id: record.id,
    revision: record.revision,
    title: record.payload.title || "未命名消费",
    occurredAt: record.occurred_at,
    payer: bill ? bill.payer_id : "",
    itemCount: items.length,
    total: (cents / 100).toFixed(2),
    currency,
  }
}
```

Show an explicit empty state and a single primary “新增消费” button. Do not duplicate expense data in a new collection.

- [ ] **Step 4: Route existing trip entry points to the list page**

Replace both `openTrip` and post-`createTrip` navigation in `miniprogram/pages/trips/index.js` with `/pages/trip-detail/index`. Add `pages/trip-detail/index` to `app.json`.

- [ ] **Step 5: Add the compact list styling**

Use one shallow container and divider-separated rows. Each row shows date/title on the left, amount on the right, then `付款人 · N 项` below. Avoid a card inside every row.

- [ ] **Step 6: Run focused page tests**

Run: `node --test miniprogram/tests/home.integration.test.js miniprogram/tests/trip-detail.integration.test.js`  
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add miniprogram/app.json miniprogram/pages/trips/index.js miniprogram/pages/trip-detail miniprogram/tests/home.integration.test.js miniprogram/tests/trip-detail.integration.test.js
git commit -m "Add trip consumption list"
```

### Task 3: Turn the expense page into a persistent single-consumption editor

**Files:**
- Modify: `miniprogram/pages/expense/index.js`
- Modify: `miniprogram/pages/expense/index.wxml`
- Modify: `miniprogram/pages/expense/index.wxss`
- Modify: `miniprogram/tests/expense-bill.integration.test.js`

**Interfaces:**
- Consumes `listExpenses`, `createExpense`, `updateExpense`, `previewSettlement`.
- Uses `expenseId` and `revision` page state to select create vs update.
- Produces one payload containing `title`, `participants`, `settlement_currency`, and exactly one entry in `expenses`.

- [ ] **Step 1: Write the failing pure-helper tests**

Add tests for title defaults, record hydration, and all-expense settlement aggregation:

```js
assert.equal(defaultExpenseTitle(new Date("2026-09-27T08:00:00")), "9月27日消费")
assert.equal(hydrateExpense(record).title, "机场晚餐")
assert.equal(buildTripPreview([recordA, recordB]).expenses.length, 2)
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `node --test miniprogram/tests/expense-bill.integration.test.js`  
Expected: FAIL because the new helpers are absent.

- [ ] **Step 3: Load and edit one stored expense**

On load, if `expenseId` exists, call `listExpenses(tripId)`, find the matching record, hydrate the editor from its payload and retain its `revision`. If it is absent, show `消费记录不存在` and navigate back after the toast. Add editable title and date inputs above the compact item list.

- [ ] **Step 4: Make saving revision-aware**

Construct the existing payload with the edited title and call the smallest required branch:

```js
const save = this.data.expenseId
  ? updateExpense(this.data.tripId, this.data.expenseId, this.data.revision, this.data.occurredAt, payload)
  : createExpense(this.data.tripId, this.data.occurredAt, payload)
```

On a 409 revision conflict, show `这笔消费已被同行人修改，请返回列表后重新打开` instead of silently overwriting.

- [ ] **Step 5: Compact each item with disclosure**

Replace the permanent large card with a divider row. The closed row contains the name, amount, allocation label, checkbox and chevron. Store `expandedItemId`; tapping a row toggles it. Render item inputs, delete control and custom percentages only inside `wx:if="{{expandedItemId === item.id}}"`. Keep batch selection and apply behavior unchanged.

- [ ] **Step 6: Preview settlement across the full trip**

Before `previewSettlement`, call `listExpenses(tripId)`, replace the just-saved record by its returned version, and flatten every stored `payload.expenses` into one request:

```js
function buildTripPreview(records, currency) {
  return {
    settlement_currency: currency,
    participants: records[0].payload.participants,
    expenses: records.flatMap((record) => record.payload.expenses || []),
  }
}
```

If the participants differ between records, stop and show `请先统一每笔消费的两位同行人`.

- [ ] **Step 7: Run focused tests**

Run: `node --test miniprogram/tests/expense-bill.integration.test.js`  
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add miniprogram/pages/expense/index.js miniprogram/pages/expense/index.wxml miniprogram/pages/expense/index.wxss miniprogram/tests/expense-bill.integration.test.js
git commit -m "Persist compact trip expense editor"
```

### Task 4: Make the invitation entry a native WeChat share flow

**Files:**
- Modify: `miniprogram/pages/trip-detail/index.js`
- Modify: `miniprogram/pages/trip-detail/index.wxml`
- Modify: `miniprogram/tests/trip-detail.integration.test.js`

**Interfaces:**
- Consumes `createTripInvite(tripId)`.
- Produces `invitePath = /pages/trip-invite/index?token=<token>` for `onShareAppMessage`.

- [ ] **Step 1: Write the failing invitation-state test**

```js
await page.prepareInvite()
assert.match(page.data.invitePath, /^\/pages\/trip-invite\/index\?token=/)
assert.equal(page.data.inviteReady, true)
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `node --test miniprogram/tests/trip-detail.integration.test.js`  
Expected: FAIL because `prepareInvite` is absent.

- [ ] **Step 3: Implement two native steps, not clipboard pseudo-links**

`prepareInvite` creates a token and reveals a `button open-type="share"` only after success. `onShareAppMessage` returns `{ title: "邀请你一起记旅行账", path: this.data.invitePath }`. If token creation fails, retain no share path and show the backend error.

- [ ] **Step 4: Run focused page tests**

Run: `node --test miniprogram/tests/trip-detail.integration.test.js`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add miniprogram/pages/trip-detail miniprogram/tests/trip-detail.integration.test.js
git commit -m "Share trip invitations natively"
```

### Task 5: Full regression and manual WeChat verification

**Files:**
- No source changes expected.

- [ ] **Step 1: Run automated checks**

Run: `node --test miniprogram/tests/*.test.js`  
Expected: PASS for every mini-program test.

Run: `uv run pytest backend/tests -q`  
Expected: PASS; backend endpoints are reused without a schema change.

Run: `uv run ruff check backend`  
Expected: `All checks passed!`.

- [ ] **Step 2: Verify the DevTools path manually**

In WeChat DevTools import `D:\Document\Money\miniprogram`, compile, create a trip, add two expense records, add multiple items to one record, open the settlement preview and confirm the total includes both records.

- [ ] **Step 3: Verify native invitation on a preview build**

Prepare the invitation, tap the native “发送邀请” control, and scan/open it with an approved test account. Confirm it joins the trip and can edit an existing consumption record.

- [ ] **Step 4: Inspect the final diff and commit only product code**

Run: `git diff --check`  
Expected: no whitespace errors.

Run: `git status --short`  
Expected: do not stage `miniprogram/project.config.json` or `miniprogram/project.private.config.json`.

```bash
git add miniprogram backend docs/superpowers
git commit -m "Complete trip expense ledger flow"
```
