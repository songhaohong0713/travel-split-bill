# Per-Expense Exchange Rate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Support one settlement currency and one persisted exchange-rate decision per expense, then render separate transfer groups for each currency.

**Architecture:** Reuse `FrankfurterRates` only from the authenticated FastAPI backend. Extend the existing settlement request contract so each `ExpenseInput` owns a `settlement_currency`; group expense objects by that value and run the existing single-currency calculator once per group. The mini program persists the chosen currency and rate fields in its existing expense payload and asks the backend for an historical quote only when it needs one.

**Tech Stack:** FastAPI, Pydantic, Python `Decimal`, `httpx`, SQLAlchemy/CloudBase payload storage, WeChat Mini Program JavaScript/WXML/WXSS, Node built-in test runner, pytest.

## Global Constraints

- Keep all money arithmetic as `Decimal` on the backend; never use JavaScript floating point as a saved rate.
- Do not add an npm or Python dependency; `httpx` and `FrankfurterRates` already exist.
- A receipt/expense has one source currency and one settlement currency; never expose per-item exchange-rate controls.
- Actual payment takes precedence and locks the expense settlement currency to the payment currency.
- A failed quote may not silently substitute a recent quote; cross-currency preview remains blocked until retry or a positive manual rate is saved.
- Preserve existing single-currency payload compatibility during the migration.

---

### Task 1: Add grouped settlement calculation

**Files:**
- Modify: `backend/app/core/settlement.py`
- Test: `backend/tests/unit/test_settlement.py`

**Interfaces:**
- Produces `calculate_grouped_settlement(participants: tuple[str, ...], expenses: tuple[Expense, ...]) -> dict[str, SettlementResult]`.
- Consumes `Expense.settlement_currency`, added as a required-or-defaulted field.

- [ ] **Step 1: Write the failing tests**

```python
def test_grouped_settlement_keeps_transfers_in_their_own_currency() -> None:
    expenses = (
        Expense(expense_id="jpy", payer_id="owner", settlement_currency="JPY", items=(LineItem("j", money("JPY", "1000"), person_allocation("friend")),), actual_payment=money("JPY", "1000")),
        Expense(expense_id="cny", payer_id="owner", settlement_currency="CNY", items=(LineItem("c", money("CNY", "20"), person_allocation("friend")),), actual_payment=money("CNY", "20")),
    )
    grouped = calculate_grouped_settlement(("owner", "friend"), expenses)
    assert grouped["JPY"].transfers == (Transfer("friend", "owner", money("JPY", "1000")),)
    assert grouped["CNY"].transfers == (Transfer("friend", "owner", money("CNY", "20.00")),)
```

- [ ] **Step 2: Run the targeted test and verify it fails**

Run: `uv run --project backend pytest backend/tests/unit/test_settlement.py -k grouped -v`

Expected: FAIL because `calculate_grouped_settlement` does not exist.

- [ ] **Step 3: Implement the smallest grouping adapter**

Add `settlement_currency: str | None = None` to `Expense`. In `calculate_grouped_settlement`, group each expense by `expense.settlement_currency or expense.items[0].amount.currency`; then call `calculate_settlement(CalculateSettlementInput(settlement_currency=currency, participants=participants, expenses=tuple(group)))` once for each sorted currency. Validate that each selected currency is supported through the existing `quantize_decimal` call.

- [ ] **Step 4: Run the targeted test and verify it passes**

Run: `uv run --project backend pytest backend/tests/unit/test_settlement.py -k grouped -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/settlement.py backend/tests/unit/test_settlement.py
git commit -m "feat: group settlement by expense currency"
```

### Task 2: Expose authenticated historical quote and grouped preview

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/api/v1/settlements.py`
- Test: `backend/tests/integration/test_settlement_preview.py`
- Test: `backend/tests/unit/test_external_providers.py`

**Interfaces:**
- Produces `GET /v1/exchange-rates?date=YYYY-MM-DD&from_currency=JPY&to_currency=CNY`.
- Produces settlement preview/publish response `{ "groups": [{ "currency": "CNY", "transfers": [...] }] }` while retaining `transfers` for a one-currency request.
- Consumes `ExpenseInput.settlement_currency`, `reference_rate`, `reference_rate_source`, `actual_payment`.

- [ ] **Step 1: Write the failing API tests**

```python
def test_rate_quote_requires_authenticated_user_and_returns_effective_date(client):
    response = client.get("/v1/exchange-rates?date=2026-09-28&from_currency=JPY&to_currency=CNY", headers=_headers())
    assert response.status_code == 200
    assert response.json()["data"].keys() >= {"rate", "requested_date", "effective_date", "from_currency", "to_currency", "provider"}

def test_preview_returns_separate_currency_groups(client):
    response = client.post(f"/v1/trips/{trip_id}/settlements/preview", headers=_headers(), json=payload_with_jpy_and_cny_expenses)
    assert [group["currency"] for group in response.json()["data"]["groups"]] == ["CNY", "JPY"]
```

- [ ] **Step 2: Run the targeted tests and verify they fail**

Run: `uv run --project backend pytest backend/tests/integration/test_settlement_preview.py backend/tests/unit/test_external_providers.py -v`

Expected: FAIL because the endpoint and `groups` response do not exist.

- [ ] **Step 3: Implement backend wiring**

Create one `httpx.AsyncClient`-backed rate provider on application startup, parallel to existing provider configuration. Add the quote route using `CurrentUser`, `date.fromisoformat`, `FrankfurterRates.quote`, and map `RateUnavailable` to a 422 response with `{code: "RATE_UNAVAILABLE"}`. Add optional `settlement_currency` to `ExpenseInput`, pass it to `Expense`, call `calculate_grouped_settlement`, and serialize each result as `{currency, responsibility_by_participant, paid_by_participant, net_by_participant, transfers, audit_lines}`. For exactly one group, also keep the current top-level `transfers` field for existing clients.

- [ ] **Step 4: Run the targeted tests and verify they pass**

Run: `uv run --project backend pytest backend/tests/integration/test_settlement_preview.py backend/tests/unit/test_external_providers.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/app/api/v1/settlements.py backend/tests/integration/test_settlement_preview.py backend/tests/unit/test_external_providers.py
git commit -m "feat: expose saved historical rate quotes"
```

### Task 3: Persist one currency decision per expense from the mini program

**Files:**
- Modify: `miniprogram/services/api.js`
- Modify: `miniprogram/pages/expense/index.js`
- Test: `miniprogram/tests/expense-bill.integration.test.js`

**Interfaces:**
- Produces `getExchangeRate(date, fromCurrency, toCurrency)`.
- Produces expense payload fields `settlement_currency`, `reference_rate`, `reference_rate_source`, and optional `actual_payment`.
- Consumes quote data `{rate, requested_date, effective_date, provider}`.

- [ ] **Step 1: Write failing front-end behavior tests**

```javascript
test("expense payload saves a selected settlement currency and rate", () => {
  const payload = helpers.buildBillPayload({ ...billData, currency: "JPY", settlementCurrency: "CNY", referenceRate: "0.04762", referenceRateSource: "frankfurter" })
  assert.equal(payload.expenses[0].settlement_currency, "CNY")
  assert.equal(payload.expenses[0].reference_rate, "0.04762")
})

test("actual payment locks the expense settlement currency", () => {
  const next = helpers.applyActualPayment({ settlementCurrency: "JPY" }, "140.82", "CNY")
  assert.equal(next.settlementCurrency, "CNY")
  assert.equal(next.settlementCurrencyLocked, true)
})
```

- [ ] **Step 2: Run the targeted test and verify it fails**

Run: `node --test miniprogram/tests/expense-bill.integration.test.js`

Expected: FAIL because the new helper and fields do not exist.

- [ ] **Step 3: Implement minimal page state and API calls**

Add `settlementCurrency`, `settlementCurrencyLocked`, `referenceRate`, `referenceRateSource`, `rateEffectiveDate`, `rateStatus`, `actualPaymentAmount`, and `actualPaymentCurrency` to page data. Default settlement currency from the trip query. After OCR establishes one source currency, request a quote only if source and settlement currencies differ and no actual payment exists. Persist the quote result into `buildBillPayload`. On quote failure set `rateStatus` to an actionable error without clearing the items; `validateBill` rejects only a cross-currency preview without actual payment or a positive saved rate. `getExchangeRate` uses the existing authenticated `request` helper.

- [ ] **Step 4: Run the targeted test and verify it passes**

Run: `node --test miniprogram/tests/expense-bill.integration.test.js`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add miniprogram/services/api.js miniprogram/pages/expense/index.js miniprogram/tests/expense-bill.integration.test.js
git commit -m "feat: save exchange decisions per expense"
```

### Task 4: Render the per-expense settlement panel

**Files:**
- Modify: `miniprogram/pages/expense/index.wxml`
- Modify: `miniprogram/pages/expense/index.wxss`
- Test: `miniprogram/tests/expense-bill.integration.test.js`

**Interfaces:**
- Consumes page state from Task 3.
- Produces a visible “本笔结算” panel between subtotal and optional adjustments.

- [ ] **Step 1: Write the failing layout test**

```javascript
test("expense page shows settlement currency and rate after the subtotal", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "..", "pages", "expense", "index.wxml"), "utf8")
  assert.match(wxml, /class="expense-subtotal"[\s\S]*class="expense-settlement"/)
  assert.match(wxml, /实际支付金额（可选）/)
  assert.match(wxml, /重新查询/)
})
```

- [ ] **Step 2: Run the targeted test and verify it fails**

Run: `node --test miniprogram/tests/expense-bill.integration.test.js`

Expected: FAIL because `expense-settlement` is absent.

- [ ] **Step 3: Implement the compact panel**

Render a single `expense-settlement` card immediately after `expense-subtotal`. Include a picker for settlement currency unless locked, one input pair for actual amount/currency, the conversion summary, a rate source/date label, retry button for failed automatic quotes, and a manual-rate input only in failure state. Use existing cream, teal, and mint ledger palette; no per-item controls and no new component dependency.

- [ ] **Step 4: Run the targeted test and verify it passes**

Run: `node --test miniprogram/tests/expense-bill.integration.test.js`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add miniprogram/pages/expense/index.wxml miniprogram/pages/expense/index.wxss miniprogram/tests/expense-bill.integration.test.js
git commit -m "feat: show per-expense settlement details"
```

### Task 5: Render grouped transfers and preserve published results

**Files:**
- Modify: `miniprogram/pages/settlement/index.js`
- Modify: `miniprogram/pages/settlement/index.wxml`
- Modify: `miniprogram/pages/settlement/index.wxss`
- Modify: `backend/app/api/v1/settlements.py`
- Test: `miniprogram/tests/expense-bill.integration.test.js`
- Test: `backend/tests/integration/test_settlement_publish.py`

**Interfaces:**
- Consumes preview/publish `groups` response.
- Produces `currencyGroups` in settlement page state and stores the same groups in the published version projection.

- [ ] **Step 1: Write failing grouped-output tests**

```javascript
test("settlement page renders a transfer section per currency", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "..", "pages", "settlement", "index.wxml"), "utf8")
  assert.match(wxml, /wx:for="{{currencyGroups}}"/)
  assert.match(wxml, /{{item.currency}} 结算/)
})
```

```python
def test_publish_persists_currency_groups(client):
    response = client.post(f"/v1/trips/{trip_id}/settlements/publish", headers=_headers(), json=payload_with_jpy_and_cny_expenses)
    assert [group["currency"] for group in response.json()["data"]["result"]["groups"]] == ["CNY", "JPY"]
```

- [ ] **Step 2: Run the targeted tests and verify they fail**

Run: `node --test miniprogram/tests/expense-bill.integration.test.js; uv run --project backend pytest backend/tests/integration/test_settlement_publish.py -v`

Expected: FAIL because grouped page state and published projection do not exist.

- [ ] **Step 3: Implement grouped output**

Store the serialized backend groups under `projection["groups"]`. In `onLoad`, normalize legacy `transfers` into one group only when `result.groups` is absent. Render one calm ledger section per group, with currency name and its transfer rows; show a settled message only when every group has zero transfers. Keep publishing and share-link behavior unchanged.

- [ ] **Step 4: Run the targeted tests and verify they pass**

Run: `node --test miniprogram/tests/expense-bill.integration.test.js; uv run --project backend pytest backend/tests/integration/test_settlement_publish.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add miniprogram/pages/settlement/index.js miniprogram/pages/settlement/index.wxml miniprogram/pages/settlement/index.wxss backend/app/api/v1/settlements.py miniprogram/tests/expense-bill.integration.test.js backend/tests/integration/test_settlement_publish.py
git commit -m "feat: show settlement transfers by currency"
```

### Task 6: Full verification and CloudBase review

**Files:**
- Review only: `backend/app/api/v1/settlements.py`, `backend/app/main.py`, `miniprogram/services/api.js`, `miniprogram/pages/expense/index.js`

- [ ] **Step 1: Run full regression suites**

Run: `node --test miniprogram/tests/*.test.js; uv run --project backend pytest backend/tests; uv run --project backend ruff check backend/app backend/tests; git diff --check`

Expected: all Node tests and pytest tests pass; Ruff and whitespace check report no errors.

- [ ] **Step 2: Run CloudBase semantic review**

Review that the quote route uses `CurrentUser`, no provider credential reaches a response, external requests only originate in Cloud Run, and stored payloads do not contain API keys.

- [ ] **Step 3: Commit final documentation update**

```bash
git add docs/superpowers/specs/2026-09-28-per-expense-exchange-rate-design.md
git commit -m "docs: record exchange rate verification"
```
