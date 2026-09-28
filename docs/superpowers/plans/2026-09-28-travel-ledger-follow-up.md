# Travel Ledger Follow-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Support owner-only deletion, repeat expense editing, personal expenses, foreign-currency estimates, and readable adjustment inputs.

**Architecture:** Reuse the existing FastAPI trip router, CloudBase PG RPC layer, and Mini Program expense page. Add two owner-checked delete operations, preserve the current revision lock, and derive estimates from the stored reference rate instead of adding tables or services.

**Tech Stack:** FastAPI, SQLAlchemy, CloudBase PostgreSQL HTTP API/RPC, WeChat Mini Program JavaScript/WXML/WXSS, pytest, Node test runner.

## Global Constraints

- Only a trip creator can delete a trip or expense.
- Trip deletion permanently removes its expenses, invitations, and settlement versions.
- No new dependencies, tables, recycle bin, audit system, or exchange-rate storage.
- A known CloudBase `TSB_REVISION_CONFLICT` maps to HTTP 409; unknown database failures stay unavailable errors.

---

## File structure

- `backend/app/providers/cloudbase_pg.py`: retain the returned safe RPC error text.
- `backend/app/api/v1/trips.py`: map conflicts and expose deletion endpoints.
- `infra/cloudbase/20260928_trip_ledger_follow_up.sql`: owner-only deletion RPCs.
- `backend/tests/integration/test_ownership.py`, `backend/tests/integration/test_trips_cloudbase.py`, and `backend/tests/unit/test_cloudbase_migration_contract.py`: backend coverage.
- `miniprogram/services/api.js`: delete request wrappers.
- `miniprogram/pages/expense/index.js`, `.wxml`, `.wxss`: local revision sync, personal bill behavior, estimate, and adjustment input layout.
- `miniprogram/pages/trip-detail/index.js`, `.wxml`, `.wxss`: owner deletion controls.
- `miniprogram/tests/expense-bill.integration.test.js` and `miniprogram/tests/trip-detail.integration.test.js`: Mini Program coverage.

### Task 1: Map CloudBase revision conflicts to 409

**Files:**
- Modify: `backend/app/providers/cloudbase_pg.py`
- Modify: `backend/app/api/v1/trips.py`
- Test: `backend/tests/integration/test_trips_cloudbase.py`

**Interfaces:** `CloudBasePgClient.rpc()` raises `CloudBasePgRequestError(400, "TSB_REVISION_CONFLICT")`; the update route returns HTTP 409 and `error.code == "REVISION_CONFLICT"`.

- [ ] Add a failing CloudBase test that makes the RPC raise `CloudBasePgRequestError(400, "TSB_REVISION_CONFLICT")`, PATCHes an expense, and asserts 409 plus `REVISION_CONFLICT`.
- [ ] Run `uv run --project backend pytest backend/tests/integration/test_trips_cloudbase.py -k revision_conflict -v`; it must fail because the current route exposes a 500.
- [ ] Change `CloudBasePgClient.request()` to pass `response.text[:500]` into `CloudBasePgRequestError`; in `update_expense`, catch that class and map only messages containing `TSB_REVISION_CONFLICT` to `HTTPException(409, detail={"code": "REVISION_CONFLICT", "message": "消费记录已被更新"})`.
- [ ] Run `uv run --project backend pytest backend/tests/integration/test_trips_cloudbase.py backend/tests/integration/test_ownership.py -v`; expect PASS.
- [ ] Commit: `git add backend/app/providers/cloudbase_pg.py backend/app/api/v1/trips.py backend/tests/integration/test_trips_cloudbase.py` then `git commit -m "fix: report expense revision conflicts"`.

### Task 2: Add owner-only permanent deletion

**Files:**
- Create: `infra/cloudbase/20260928_trip_ledger_follow_up.sql`
- Modify: `backend/app/api/v1/trips.py`
- Test: `backend/tests/integration/test_ownership.py`
- Test: `backend/tests/integration/test_trips_cloudbase.py`
- Test: `backend/tests/unit/test_cloudbase_migration_contract.py`

**Interfaces:** `DELETE /v1/trips/{trip_id}` and `DELETE /v1/trips/{trip_id}/expenses/{expense_id}` return 204 for the creator and 404 for everyone else. CloudBase RPCs are `tsb_delete_owner_trip(p_trip_id text, p_owner_id text)` and `tsb_delete_owner_expense(p_trip_id text, p_expense_id text, p_owner_id text)`.

- [ ] Add failing tests: member deletion is 404; owner expense deletion is 204; owner trip deletion is 204; deleted records cannot be listed.
- [ ] Run `uv run --project backend pytest backend/tests/integration/test_ownership.py -k delete -v`; expect FAIL because the routes do not exist.
- [ ] Add the two SQL functions. Each first checks `trips.id = p_trip_id AND trips.owner_id = p_owner_id`; return `{ "not_found": true }` if false. Expense function deletes the matching expense; trip function deletes the matching trip and relies on existing foreign-key cascades. Revoke public access and grant both functions to `service_role`.
- [ ] Add FastAPI delete handlers. In CloudBase mode call the matching RPC and translate `not_found` to `_not_found()`. In local mode select `Trip` by both id and owner_id, delete the record, commit, and respond with `Response(status_code=204)`.
- [ ] Register both RPC names in `REQUIRED_RPCS` and add CloudBase mocked-route tests.
- [ ] Run `uv run --project backend pytest backend/tests/integration/test_ownership.py backend/tests/integration/test_trips_cloudbase.py backend/tests/unit/test_cloudbase_migration_contract.py -v`; expect PASS.
- [ ] Commit: `git add infra/cloudbase/20260928_trip_ledger_follow_up.sql backend/app/api/v1/trips.py backend/tests/integration/test_ownership.py backend/tests/integration/test_trips_cloudbase.py backend/tests/unit/test_cloudbase_migration_contract.py` then `git commit -m "feat: let trip owners delete ledger data"`.

### Task 3: Fix repeat saves and allow personal bills

**Files:**
- Modify: `miniprogram/pages/expense/index.js`
- Test: `miniprogram/tests/expense-bill.integration.test.js`

**Interfaces:** a successful save updates local `expenseId`, `revision`, and `occurredAt`. `buildBillPayload(data)` returns `participants: [payer]` and `{[payer]: "1"}` allocation when `friend` is empty.

- [ ] Add failing tests that assert a save returning revision 2 updates `instance.data.revision`, and a blank companion produces one participant with a 100% payer allocation.
- [ ] Run `node --test miniprogram/tests/expense-bill.integration.test.js`; expect FAIL.
- [ ] In the successful save chain, call `syncBill({ expenseId: saved.id, revision: saved.revision, occurredAt: saved.occurred_at })` before loading all expenses. Remove the required-companion validation; retain the duplicate-name validation only when a companion exists. Make payload participants, item allocations, and shared adjustment allocation use a payer-only allocation when companion is blank.
- [ ] Run `node --test miniprogram/tests/*.test.js`; expect PASS.
- [ ] Commit: `git add miniprogram/pages/expense/index.js miniprogram/tests/expense-bill.integration.test.js` then `git commit -m "fix: support personal expenses and repeat saves"`.

### Task 4: Add estimate, native confirmations, and readable inputs

**Files:**
- Modify: `miniprogram/services/api.js`
- Modify: `miniprogram/pages/expense/index.js`
- Modify: `miniprogram/pages/expense/index.wxml`
- Modify: `miniprogram/pages/expense/index.wxss`
- Modify: `miniprogram/pages/trip-detail/index.js`
- Modify: `miniprogram/pages/trip-detail/index.wxml`
- Modify: `miniprogram/pages/trip-detail/index.wxss`
- Test: `miniprogram/tests/trip-detail.integration.test.js`

**Interfaces:** `deleteTrip(tripId)` and `deleteExpense(tripId, expenseId)` wrap existing `request`. `estimatedSettlementAmount(total, rate)` returns a two-decimal string only for valid positive values.

- [ ] Add failing tests for `estimatedSettlementAmount("6155.00", "0.04254") === "261.87"`, and for a confirmed owner expense deletion removing the item from the refreshed list.
- [ ] Run `node --test miniprogram/tests/expense-bill.integration.test.js miniprogram/tests/trip-detail.integration.test.js`; expect FAIL.
- [ ] Add the two API wrappers. Use native `wx.showModal` before each delete; do not add a modal component. Only render deletion actions when `isOwner` is true.
- [ ] Render `6155 JPY ≈ ¥261.87 CNY` below the existing rate only when no actual payment has been supplied and the estimate is available. Keep it display-only.
- [ ] Hide companion-only allocation controls when the companion field is blank. Make tax and refund inputs full-width rows with `min-height: 88rpx` and enough bottom padding to stay clear of the fixed action bar.
- [ ] Run `node --test miniprogram/tests/*.test.js`; expect PASS.
- [ ] Manually verify in WeChat Developer Tools: personal bill preview, JPY estimate, tax/refund typing, owner deletion confirmation, and second save of the same bill.
- [ ] Commit: `git add miniprogram/services/api.js miniprogram/pages/expense miniprogram/pages/trip-detail miniprogram/tests` then `git commit -m "feat: polish travel ledger editing"`.

### Task 5: Full verification and CloudBase handoff

**Files:** no planned code changes.

- [ ] Run `uv run --project backend pytest backend/tests && uv run --project backend ruff check backend/app backend/tests`; expect all tests and lint checks to pass.
- [ ] Review backend responses to ensure no API key, request body, or raw PostgreSQL error is exposed; only `TSB_REVISION_CONFLICT` gets the friendly 409 response.
- [ ] Apply `infra/cloudbase/20260928_trip_ledger_follow_up.sql` in the CloudBase SQL editor, then deploy the backend revision through the existing CloudBase Run flow.
- [ ] Recompile the Mini Program and smoke-test owner and invited-member accounts on device.
