# Member Expense Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow either member of a trip to update any expense while returning controlled API errors for denied or malformed CloudBase RPC results.

**Architecture:** Keep the existing FastAPI, JWT, PostgreSQL RPC, and revision-locking flow. Change only the update permission predicate from expense/trip ownership to trip membership, mirror that rule in the local SQLAlchemy path, and validate the RPC response before constructing the HTTP response.

**Tech Stack:** FastAPI, SQLAlchemy, PostgreSQL PL/pgSQL, pytest

## Global Constraints

- Preserve the existing API request and success response shape.
- Preserve revision conflict handling and return 409 for stale revisions.
- Return 404 to callers who are not trip members.
- Do not modify existing database tables.
- Do not modify unrelated dirty workspace files.

---

### Task 1: Capture the CloudBase update contract

**Files:**
- Modify: `backend/tests/integration/test_trips_cloudbase.py`
- Modify: `backend/tests/unit/test_cloudbase_migration_contract.py`

**Interfaces:**
- Consumes: `PATCH /v1/trips/{trip_id}/expenses/{expense_id}` and `tsb_update_expense_revision`.
- Produces: regression coverage for `not_found`, malformed responses, and membership-based SQL authorization.

- [ ] **Step 1: Write failing tests for `not_found`, malformed RPC responses, and membership authorization.**
- [ ] **Step 2: Run the focused tests and verify they fail for the missing handling and old permission predicate.**

### Task 2: Implement the minimal permission and response fix

**Files:**
- Modify: `backend/app/api/v1/trips.py:333-423`
- Modify: `infra/cloudbase/20260929_collaboration_permissions.sql:14-23`

**Interfaces:**
- Consumes: flat RPC success object `{id, revision, occurred_at, payload}` or `{not_found: true}`.
- Produces: unchanged successful HTTP response, 404 for non-members, controlled 503 for malformed RPC output.

- [ ] **Step 1: Handle `not_found` before success-field access and reject missing success fields explicitly.**
- [ ] **Step 2: Change both CloudBase and local authorization checks to trip membership.**
- [ ] **Step 3: Run the focused tests and verify they pass.**

### Task 3: Regression verification

**Files:**
- Test: `backend/tests/integration/test_trips_cloudbase.py`
- Test: `backend/tests/integration/test_trip_collaboration.py`
- Test: `backend/tests/integration/test_ownership.py`
- Test: `backend/tests/unit/test_cloudbase_migration_contract.py`

**Interfaces:**
- Consumes: completed implementation.
- Produces: evidence that member updates, non-member denial, and revision conflicts remain correct.

- [ ] **Step 1: Run focused backend tests.**
- [ ] **Step 2: Run the complete backend test suite.**
- [ ] **Step 3: Review the final diff and report deployment requirements and remaining risks.**
