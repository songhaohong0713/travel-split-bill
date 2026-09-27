# 小票识别自动填充 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将有效小票识别条目直接填入消费明细，并把每项金额定义为行小计。

**Architecture:** 后端维持现有候选条目 JSON，但提示词把 `amount` 明确为行小计且过滤非商品行。小程序在识别完成时将候选批量转换为可编辑商品；仅有空白默认项时替换，否则追加，且不再导航至 OCR 核对页。

**Tech Stack:** Python 3.12、FastAPI、httpx、原生微信小程序、Node.js 内置测试。

## Global Constraints

- `amount` 始终表示分账使用的行小计。
- 不覆盖已有手动填写商品，不更改上传 API、数据库或分账算法。
- 识别失败仅显示安全提示，日志不包含小票、供应商正文或密钥。

---

### Task 1: 明确后端的小计提取契约

**Files:**
- Modify: `backend/app/providers/deepseek_receipt_ai.py:45-52`
- Modify: `backend/tests/unit/test_deepseek_receipt_ai.py`

**Interfaces:**
- Produces: 候选 `{source_text, translated_text, amount, currency}`，其中 `amount` 是行小计。

- [ ] **Step 1: Write the failing test**

```python
def test_receipt_prompt_requires_line_totals() -> None:
    provider = DeepSeekReceiptAi("key", client=FakeClient('{"items":[{"source_text":"Tea x2","translated_text":"茶","amount":"240","currency":"JPY"}]}'))
    asyncio.run(provider.recognize(b"jpeg"))
    prompt = provider._client.calls[0]["json"]["messages"][0]["content"][0]["text"]
    assert "line total" in prompt
    assert "tax" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --project backend pytest backend/tests/unit/test_deepseek_receipt_ai.py::test_receipt_prompt_requires_line_totals -v`

Expected: FAIL because the existing prompt does not define `amount` as a line total.

- [ ] **Step 3: Write minimal implementation**

Update the prompt to require each `amount` to be the purchased line's total, calculate `quantity × unit price` when present, and exclude totals, tax, fees, discounts and payment rows.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --project backend pytest backend/tests/unit/test_deepseek_receipt_ai.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/providers/deepseek_receipt_ai.py backend/tests/unit/test_deepseek_receipt_ai.py
git commit -m "feat: extract receipt line totals"
```

### Task 2: 将识别候选批量自动填入商品明细

**Files:**
- Modify: `miniprogram/pages/expense/index.js:148-151`
- Modify: `miniprogram/pages/expense/index.wxml:14-23`
- Modify: `miniprogram/tests/ocr-review.integration.test.js`

**Interfaces:**
- Produces: `applyOcrCandidates(candidates)` 在当前页面将候选转换为 `newItem()` 商品。

- [ ] **Step 1: Write the failing tests**

```javascript
test("expense page replaces its blank item with all recognized receipt items", () => {
  const { definition } = loadPage()
  const page = pageInstance(definition)
  page.applyOcrCandidates([{ translated_text: "茶", amount: "120" }, { translated_text: "牛奶", amount: "230" }])
  assert.deepEqual(page.data.items.map(({ name, amount }) => ({ name, amount })), [{ name: "茶", amount: "120" }, { name: "牛奶", amount: "230" }])
})

test("expense page appends recognized items without overwriting manual items", () => {
  const page = pageInstance(definition, { items: [{ id: "manual", name: "手动商品", amount: "10", selected: false, allocationMode: "payer", payerPercent: "100", friendPercent: "0" }] })
  page.applyOcrCandidates([{ translated_text: "茶", amount: "120" }])
  assert.equal(page.data.items.length, 2)
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test miniprogram/tests/ocr-review.integration.test.js`

Expected: FAIL because `applyOcrCandidates` does not exist and the page still navigates to OCR review.

- [ ] **Step 3: Write minimal implementation**

Add `applyOcrCandidates`: filter candidates with nonempty translated/original name and positive `amount`; turn each into `{...newItem(), name, amount}`; replace only one blank default item or append otherwise; call `syncBill`, set `expandedItemId` to the first new id, and set status to `已自动添加 N 项，请核对金额`.

In `pollOcr`, call `applyOcrCandidates(candidates)` for `needs_review` and remove `openOcrReview`. Remove the “待核对 / 去核对” card from WXML.

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test miniprogram/tests/ocr-review.integration.test.js miniprogram/tests/expense-bill.integration.test.js`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add miniprogram/pages/expense/index.js miniprogram/pages/expense/index.wxml miniprogram/tests/ocr-review.integration.test.js
git commit -m "feat: auto-fill receipt items into expense details"
```

### Task 3: 全量验证

**Files:**
- No production files.

- [ ] **Step 1: Run backend suite**

Run: `uv run --project backend pytest backend/tests && uv run --project backend ruff check backend/app backend/tests`

Expected: all tests pass and `All checks passed!`.

- [ ] **Step 2: Run all mini program tests**

Run: `node --test miniprogram/tests/*.test.js`

Expected: all tests pass.

- [ ] **Step 3: Inspect changes**

Run: `git diff --check && git status --short`

Expected: no whitespace errors and only intended files changed.
