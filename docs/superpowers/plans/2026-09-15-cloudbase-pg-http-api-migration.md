# CloudBase PostgreSQL HTTP API Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the FastAPI service from direct SQLAlchemy/SQLite persistence to CloudBase PostgreSQL HTTP API while preserving all existing mini-program endpoints and response contracts.

**Architecture:** Introduce a single CloudBase PostgREST client and a repository facade. Route handlers depend on the facade instead of a SQLAlchemy session. Read operations use table endpoints; state transitions use `tsb_*` SQL RPCs so every multi-row write remains atomic in PostgreSQL.

**Tech Stack:** Python 3.12, FastAPI, httpx, CloudBase PostgreSQL HTTP API/PostgREST, PostgreSQL PL/pgSQL, pytest, ruff, mypy.

## Global Constraints

- Configure only `CLOUDBASE_ENV_ID` and `CLOUDBASE_API_KEY` in Cloud Hosting; never commit or log API keys.
- Build all CloudBase calls under `https://<env-id>.api.tcloudbasegateway.com/v1/rdb/rest` with `Authorization: Bearer <api-key>`.
- Keep all existing `/v1/*` mini-program paths, request models, status codes, and JSON response shapes unchanged.
- Production must fail readiness when CloudBase configuration is absent; it must never select SQLite.
- Preserve the user-owned `backend/tests/integration/test_ownership.py` modification and do not stage it.

---

### Task 1: CloudBase HTTP client and readiness

**Files:**
- Create: `backend/app/providers/cloudbase_pg.py`
- Create: `backend/tests/unit/test_cloudbase_pg.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/db/session.py`

**Interfaces:**
- Produces `CloudBasePgClient.from_environment() -> CloudBasePgClient`.
- Produces `async request(method: str, path: str, *, params: dict[str, str] | None = None, payload: object | None = None) -> object`.
- Produces `async rpc(name: str, payload: dict[str, object]) -> object`.
- Produces `CloudBasePgConfigurationError` and `CloudBasePgUnavailable`.

- [ ] **Step 1: Write failing unit tests**

```python
def test_from_environment_builds_postgrest_base_url(monkeypatch):
    monkeypatch.setenv("CLOUDBASE_ENV_ID", "travel-split-bill")
    monkeypatch.setenv("CLOUDBASE_API_KEY", "server-key")
    assert CloudBasePgClient.from_environment().base_url == (
        "https://travel-split-bill.api.tcloudbasegateway.com/v1/rdb/rest"
    )


def test_client_sends_bearer_key_and_maps_503():
    client = CloudBasePgClient("env", "server-key", httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(503))
    ))
    with pytest.raises(CloudBasePgUnavailable):
        asyncio.run(client.request("GET", "/trips"))
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest backend/tests/unit/test_cloudbase_pg.py -q`

Expected: FAIL because `CloudBasePgClient` does not exist.

- [ ] **Step 3: Implement the client**

```python
class CloudBasePgClient:
    def __init__(self, env_id: str, api_key: str, client: httpx.AsyncClient | None = None):
        self.base_url = f"https://{env_id}.api.tcloudbasegateway.com/v1/rdb/rest"
        self._api_key = api_key
        self._client = client

    async def rpc(self, name: str, payload: dict[str, object]) -> object:
        return await self.request("POST", f"/rpc/{name}", payload=payload)
```

`request` adds bearer authorization and JSON content headers, raises configuration errors for missing values, maps 401/403 to configuration errors and timeouts/5xx to unavailable errors, and never includes headers in exception messages.

- [ ] **Step 4: Replace production readiness**

Create the HTTP client during FastAPI lifespan and save it as `app.state.data_store`. Change `/readyz` to call a harmless CloudBase API request through that store. Keep `configure_database` and SQLite only for explicit tests; remove environment-based SQLite fallback from production startup.

- [ ] **Step 5: Verify**

Run: `uv run pytest backend/tests/unit/test_cloudbase_pg.py backend/tests/integration/test_health.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add backend/app/providers/cloudbase_pg.py backend/tests/unit/test_cloudbase_pg.py backend/app/main.py backend/app/db/session.py backend/tests/integration/test_health.py
git commit -m "Add CloudBase PostgreSQL HTTP client"
```

### Task 2: Transaction RPC migration SQL

**Files:**
- Create: `infra/cloudbase/cloudbase_http_api_migration.sql`
- Create: `backend/tests/unit/test_cloudbase_migration_contract.py`

**Interfaces:**
- Produces database RPCs `tsb_upsert_wechat_user_and_issue_refresh_token`, `tsb_rotate_refresh_token`, `tsb_create_trip`, `tsb_create_expense_with_idempotency`, `tsb_update_expense_revision`, `tsb_publish_settlement_version`, `tsb_create_share_link`, `tsb_create_receipt_image_and_job`.
- Every RPC accepts `p_` prefixed JSON-safe input fields and returns one JSONB object.

- [ ] **Step 1: Write migration contract tests**

```python
def test_migration_defines_all_mutation_rpcs():
    sql = Path("infra/cloudbase/cloudbase_http_api_migration.sql").read_text()
    for name in REQUIRED_RPCS:
        assert f"FUNCTION public.{name}" in sql
        assert "REVOKE ALL ON FUNCTION" in sql
        assert "GRANT EXECUTE ON FUNCTION" in sql
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest backend/tests/unit/test_cloudbase_migration_contract.py -q`

Expected: FAIL because the migration SQL does not exist.

- [ ] **Step 3: Create migration SQL**

Use `CREATE OR REPLACE FUNCTION public.tsb_* (...) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER`. Each function first rejects any role other than `service_role`, writes all affected rows, and returns the endpoint-ready JSON fields. `tsb_create_expense_with_idempotency` uses the existing `(owner_id, key)` unique constraint and returns the persisted response for duplicate keys. `tsb_update_expense_revision` raises a PostgreSQL exception tagged `TSB_REVISION_CONFLICT` if no matching revision exists. Revoke execute from `PUBLIC` and grant execute only to `service_role` for every function.

- [ ] **Step 4: Verify**

Run: `uv run pytest backend/tests/unit/test_cloudbase_migration_contract.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add infra/cloudbase/cloudbase_http_api_migration.sql backend/tests/unit/test_cloudbase_migration_contract.py
git commit -m "Add CloudBase transaction RPC migration"
```

### Task 3: Repository facade and authentication/trip routes

**Files:**
- Create: `backend/app/repositories/cloudbase.py`
- Create: `backend/tests/integration/test_cloudbase_routes.py`
- Modify: `backend/app/api/dependencies.py`
- Modify: `backend/app/api/v1/auth.py`
- Modify: `backend/app/api/v1/trips.py`

**Interfaces:**
- Produces `CloudBaseRepository(client: CloudBasePgClient)`.
- Produces async methods `login_wechat`, `refresh_tokens`, `create_trip`, `list_trips`, `list_expenses`, `create_expense`, and `update_expense`.
- Produces `CurrentRepository = Annotated[CloudBaseRepository, Depends(current_repository)]`.

- [ ] **Step 1: Write failing route tests using `httpx.MockTransport`**

```python
def test_create_trip_uses_rpc_and_keeps_response_shape(client):
    response = client.post("/v1/trips", headers=owner_header(), json={
        "name": "东京周末", "default_currency": "JPY"
    })
    assert response.status_code == 201
    assert response.json() == {"data": {
        "id": "trip-1", "name": "东京周末", "default_currency": "JPY"
    }}
```

Add tests for WeChat login token persistence, refresh rotation, owner-filtered trip/expense reads, duplicate idempotency-key replay, and 409 revision conflict.

- [ ] **Step 2: Run route tests**

Run: `uv run pytest backend/tests/integration/test_cloudbase_routes.py -q`

Expected: FAIL because routes still require `DbSession`.

- [ ] **Step 3: Implement facade and refactor routes**

Replace ORM queries in `auth.py` and `trips.py` with repository calls. Use table GET endpoints only for owner-filtered reads. Use Task 2 RPCs for token writes, trip creation, expense creation/idempotency, and expense revision updates. Convert RPC conflict/not-found signals to the existing FastAPI envelopes and status codes.

- [ ] **Step 4: Verify**

Run: `uv run pytest backend/tests/integration/test_auth.py backend/tests/integration/test_cloudbase_routes.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/repositories/cloudbase.py backend/app/api/dependencies.py backend/app/api/v1/auth.py backend/app/api/v1/trips.py backend/tests/integration/test_cloudbase_routes.py backend/tests/integration/test_auth.py
git commit -m "Move authentication and trips to CloudBase API"
```

### Task 4: Settlement, shares, uploads, and deployment cutover

**Files:**
- Modify: `backend/app/api/v1/settlements.py`
- Modify: `backend/app/api/v1/shares.py`
- Modify: `backend/app/api/v1/uploads.py`
- Modify: `backend/app/worker/receipt_processor.py`
- Modify: `infra/cloudbase/cloudrun.yaml`
- Modify: `docs/architecture/python-operations.md`
- Modify: `backend/tests/integration/test_settlement_publish.py`
- Modify: `backend/tests/integration/test_share_links.py`
- Modify: `backend/tests/integration/test_uploads.py`

**Interfaces:**
- All route handlers receive `CurrentRepository`, never `DbSession`.
- Receipt worker receives the repository facade rather than a SQLAlchemy session.

- [ ] **Step 1: Write failing HTTP-backed integration tests**

Add mock CloudBase RPC responses for settlement publish, share-link creation/public lookup, upload completion, receipt-job creation, and receipt-job status lookup. Assert unchanged existing endpoint payloads and status codes.

- [ ] **Step 2: Run tests**

Run: `uv run pytest backend/tests/integration/test_settlement_publish.py backend/tests/integration/test_share_links.py backend/tests/integration/test_uploads.py -q`

Expected: FAIL because these routes still use SQLAlchemy sessions.

- [ ] **Step 3: Refactor remaining persistence callers**

Keep settlement arithmetic in `app.core.settlement`; pass its projection to `tsb_publish_settlement_version`. Use repository read methods for public shares and job status. Use Task 2 RPCs for share and receipt state changes. Replace the worker queue selection/claim flow with explicit repository methods backed by a claim RPC.

- [ ] **Step 4: Update deployment documentation**

Remove `DATABASE_URL`, `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, and `PGPASSWORD` from CloudBase production instructions. Add required Cloud Hosting variables `CLOUDBASE_ENV_ID` and `CLOUDBASE_API_KEY`; document executing `infra/cloudbase/cloudbase_http_api_migration.sql` in the CloudBase SQL editor before deployment.

- [ ] **Step 5: Verify all checks**

Run:

```powershell
uv run pytest backend/tests -q
uv run ruff check backend
uv run mypy backend/app
node miniprogram/tests/ocr-review.integration.test.js
```

Expected: all commands exit 0.

- [ ] **Step 6: Commit**

```powershell
git add backend/app/api/v1/settlements.py backend/app/api/v1/shares.py backend/app/api/v1/uploads.py backend/app/worker/receipt_processor.py infra/cloudbase/cloudrun.yaml docs/architecture/python-operations.md backend/tests/integration/test_settlement_publish.py backend/tests/integration/test_share_links.py backend/tests/integration/test_uploads.py
git commit -m "Use CloudBase API for remaining persistence"
```

### Task 5: Production verification

**Files:**
- Modify: `docs/architecture/python-operations.md`

- [ ] **Step 1: Execute the migration**

In CloudBase PostgreSQL SQL editor, execute the complete `infra/cloudbase/cloudbase_http_api_migration.sql` once and retain the successful query record.

- [ ] **Step 2: Configure Cloud Hosting**

Set `CLOUDBASE_ENV_ID` and `CLOUDBASE_API_KEY` only in the service version environment variables. Keep `JWT_SECRET`, `WECHAT_APP_ID`, `WECHAT_APP_SECRET`, and `PORT=8080`. Do not set direct database credentials.

- [ ] **Step 3: Deploy and verify**

Deploy the GitHub `main` version, request `/healthz`, then `/readyz`; both must return 200. Open the mini program, create a trip, add an expense, publish a settlement, create a share link, and verify the public link response.

- [ ] **Step 4: Commit operational checklist**

```powershell
git add docs/architecture/python-operations.md
git commit -m "Document CloudBase API deployment verification"
```