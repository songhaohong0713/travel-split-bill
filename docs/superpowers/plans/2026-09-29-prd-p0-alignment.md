# PRD P0 Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align the core travel-ledger flow with the approved PRD: stable member identities, clear responsibility summaries, consistent totals, and correct post-save navigation.

**Architecture:** Keep backward compatibility for legacy name-keyed expense payloads while new bills use stable trip-member IDs. Add pure mini-program helpers for labels, responsibility totals, and navigation so they are testable without a WeChat runtime. Extend the existing members endpoint for CloudBase without adding a new persistence layer.

**Tech Stack:** WeChat Mini Program JavaScript/WXML/WXSS, FastAPI, SQLAlchemy, CloudBase PostgreSQL HTTP API, Node test runner, pytest.

## Global Constraints

- Preserve existing saved expense payloads that use names such as `我` and `卢`.
- Do not add dependencies or database tables for P0.
- Only trip members may read; only the expense creator may edit or delete an expense.
- A bill must use one settlement rule and one stable participant set.

---

### Task 1: Stable trip member identities

**Files:**
- Modify: `backend/app/api/v1/trips.py`
- Modify: `backend/tests/integration/test_trip_collaboration.py`
- Modify: `miniprogram/pages/expense/index.js`
- Modify: `miniprogram/pages/expense/index.wxml`
- Modify: `miniprogram/tests/expense-bill.integration.test.js`

**Interfaces:**
- Consumes: `GET /v1/trips/{trip_id}/members`
- Produces: member records `{id, is_owner, is_current}` and ID-keyed `participants`, `payer_id`, and `allocation` fields for new expenses.

- [ ] Add failing backend tests for SQLAlchemy and CloudBase member listing.
- [ ] Run the focused pytest tests and confirm the endpoint tests fail.
- [ ] Implement the CloudBase member query and current-member marker.
- [ ] Add failing mini-program tests for member labels and ID-keyed payloads.
- [ ] Run the focused Node tests and confirm the payload tests fail.
- [ ] Load members in the expense page, render a native payer selector, and keep legacy payload hydration compatible.
- [ ] Run the focused backend and mini-program tests until green.

### Task 2: Bill responsibility summary

**Files:**
- Modify: `miniprogram/pages/expense/index.js`
- Modify: `miniprogram/pages/expense/index.wxml`
- Modify: `miniprogram/pages/expense/index.wxss`
- Modify: `miniprogram/tests/expense-bill.integration.test.js`

**Interfaces:**
- Consumes: normalized item allocations, adjustments, actual payment, and member labels.
- Produces: `responsibilityRows` and `expenseAdvanceText` for both entry and reading modes.

- [ ] Add failing tests for equal, personal, and adjustment responsibility totals.
- [ ] Confirm the focused tests fail for missing summary helpers.
- [ ] Implement one pure responsibility calculation used by editor and read-only detail.
- [ ] Render always-visible per-person responsibility and per-expense advance information.
- [ ] Run the focused tests until green.

### Task 3: Consistent save and overview flow

**Files:**
- Modify: `miniprogram/pages/expense/index.js`
- Modify: `miniprogram/pages/trip-detail/index.js`
- Modify: `miniprogram/pages/trip-detail/index.wxml`
- Modify: `miniprogram/pages/trip-detail/index.wxss`
- Modify: `miniprogram/tests/expense-bill.integration.test.js`
- Modify: `miniprogram/tests/trip-detail.integration.test.js`

**Interfaces:**
- Consumes: saved expense state and settlement preview groups.
- Produces: saved edits that return to trip detail and a trip overview whose headline total matches the settlement currency.

- [ ] Add failing tests for edit-save navigation and overview aggregation.
- [ ] Confirm the focused tests fail for the old redirect and mixed display hierarchy.
- [ ] Make saved edits navigate back to trip detail; retain save-and-preview for new expenses.
- [ ] Prioritize pending transfer and settlement-currency totals in the trip overview.
- [ ] Run all Node and pytest tests.

### Task 4: Review and delivery

**Files:**
- Review: all files changed above

**Interfaces:**
- Consumes: completed P0 changes.
- Produces: a review-clean commit ready for deployment.

- [ ] Run the CloudBase mini-program review checks.
- [ ] Run `node --test miniprogram/tests/*.test.js` and expect zero failures.
- [ ] Run `uv run pytest -q` and expect zero failures.
- [ ] Inspect the diff for secrets, generated files, and unrelated local settings.
- [ ] Commit and push `codex/prd-p0-alignment`.
