# CloudBase PostgreSQL HTTP API Migration Design

## Goal

Run the existing FastAPI backend against the current CloudBase shared PostgreSQL instance without database TCP credentials, VPC configuration, or a SQLite production fallback.

## Decision

The FastAPI service remains the only backend exposed to the mini program. It uses CloudBase PostgreSQL's PostgREST-compatible HTTP API with a CloudBase `api_key` (`service_role`) held only in Cloud Hosting environment variables. The mini program continues using the existing `/v1/*` API and never receives the CloudBase key.

## Runtime configuration

Cloud Hosting will provide these variables to the backend:

- `CLOUDBASE_ENV_ID`: the CloudBase environment ID used to build `https://<env-id>.api.tcloudbasegateway.com`.
- `CLOUDBASE_API_KEY`: the server-only `api_key` value created in CloudBase API Key management.
- Existing `JWT_SECRET`, `WECHAT_APP_ID`, `WECHAT_APP_SECRET`, and `PORT` remain unchanged.

`DATABASE_URL` and all direct PostgreSQL variables are removed from the Cloud Hosting deployment configuration. In Cloud Hosting, missing CloudBase HTTP configuration is a readiness failure; the application must never silently use SQLite. SQLite remains available only when explicit test configuration calls `configure_database`.

## Component boundaries

### `CloudBasePgClient`

A new HTTP client owns the base URL, `Authorization: Bearer <CLOUDBASE_API_KEY>` header, timeouts, PostgREST filter encoding, error translation, and RPC calls. It exposes table reads/writes and `POST /v1/rdb/rest/rpc/<function>` without exposing HTTP details to route handlers.

It maps failed CloudBase calls to one application error type carrying safe status and request context. API key values, authorization headers, and raw request bodies are never logged.

### Repository layer

A focused repository interface replaces `Session` usage in route handlers. It owns persistence for users/tokens, trips/expenses/idempotency, settlement versions/share links, and receipt images/jobs. Core money, allocation, settlement, OCR parsing, WeChat authentication, and FastAPI response schemas remain unchanged.

Tests use an in-memory repository that implements the same interface, preserving fast deterministic test coverage without a real CloudBase key.

### Database RPC functions

Read-only work uses PostgREST table queries. State changes that previously required one SQLAlchemy transaction are implemented as `public` PostgreSQL RPC functions returning JSON:

- `tsb_upsert_wechat_user_and_issue_refresh_token`
- `tsb_rotate_refresh_token`
- `tsb_create_trip`
- `tsb_create_expense_with_idempotency`
- `tsb_update_expense_revision`
- `tsb_publish_settlement_version`
- `tsb_create_share_link`
- `tsb_create_receipt_image_and_job`

Each function validates the service role before modifying data, uses a single database transaction, returns the response payload required by the existing backend endpoint, and is called through `/v1/rdb/rest/rpc/<function>`. The migration SQL grants `service_role` table and function permissions, revokes public execute permissions, and preserves the existing tables and data.

## Request flow

1. The mini program sends its existing request to the FastAPI Cloud Hosting URL.
2. FastAPI authenticates its own JWT and applies ownership rules exactly as today.
3. The repository sends a server-to-server request to CloudBase PG REST/RPC using `CLOUDBASE_API_KEY`.
4. CloudBase maps the key to `service_role`, executes the table query or transaction RPC, and returns JSON.
5. FastAPI retains its established response and error envelopes for the mini program.

## Error handling and observability

- `/healthz` remains process-only.
- `/readyz` performs a lightweight CloudBase API request and returns 503 if API key, environment ID, or HTTP connectivity is invalid.
- A CloudBase 401/403 becomes a 503 `DATABASE_CONFIGURATION_INVALID`; 5xx/timeout becomes 503 `DATABASE_UNAVAILABLE`; table/RPC schema errors become 500 `DATABASE_SCHEMA_ERROR` without exposing credentials.
- WeChat login failures remain `WECHAT_LOGIN_FAILED`; they cannot be confused with database failures.

## Security

- `CLOUDBASE_API_KEY` is set only in Cloud Hosting's version environment variables and is never committed, returned, or printed.
- CloudBase service role bypasses RLS, so authorization stays enforced by existing FastAPI ownership checks plus RPC role checks.
- SQL migration revokes `PUBLIC` execution on every `tsb_*` RPC and grants execute only to `service_role`.

## Compatibility and rollout

The mini program request URLs, payloads, and response JSON remain compatible. The existing CloudBase schema is retained. A new migration SQL file is executed once in the CloudBase SQL editor before routing production traffic to the HTTP-backed service. Deployment is permitted only after the backend test suite, HTTP mock integration tests, and `/readyz` verification pass.

## Out of scope

This migration does not add payment collection, real COS upload transport, OCR provider changes, direct client-side database access, or new user-visible features.