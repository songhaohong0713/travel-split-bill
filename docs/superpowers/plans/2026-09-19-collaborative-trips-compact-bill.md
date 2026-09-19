# 协作旅行与紧凑账单 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 两名微信用户可通过一次性邀请共同编辑同一旅行，并使用紧凑、无遮挡的多商品账单录入页。

**Architecture:** FastAPI 的本地 SQLAlchemy 与 CloudBase PostgreSQL RPC 使用相同的成员授权规则。小程序通过成员/邀请 API 进入旅行协作；账单页保留多商品与结算逻辑，只重排信息层级与可见状态。

**Tech Stack:** Python、FastAPI、SQLAlchemy、Alembic、CloudBase PostgreSQL RPC、微信小程序原生 JavaScript/WXML/WXSS、Node test、pytest。

## Global Constraints

- 每段旅行仅两名成员，二者均可编辑全部旅行内容。
- 邀请令牌只保存 SHA-256 哈希，7 天有效且只能接受一次。
- 所有授权由服务端成员关系完成；非成员返回 404。
- CloudBase 表和 RPC 使用 `cloudbase/migrations/` 的版本化迁移。
- `.checkout-bar` 必须处于文档流，不能使用 `position: fixed`。

---

### Task 1: 成员与邀请持久化

**Files:**
- Modify: `backend/app/db/models.py`
- Create: `backend/alembic/versions/<revision>_add_trip_members_and_invites.py`
- Create: `backend/tests/integration/test_trip_collaboration.py`

**Interfaces:** `TripMember(trip_id, user_id, joined_at)` 与 `TripInvite(trip_id, creator_id, token_hash, expires_at, used_at, revoked_at)`；成员关系以 `(trip_id, user_id)` 唯一。

- [ ] 写失败测试：同一用户为同一旅行插入两条 `TripMember` 时提交抛出 `IntegrityError`。
- [ ] 运行 `uv run pytest backend/tests/integration/test_trip_collaboration.py::test_trip_member_is_unique -q`，确认因模型不存在而失败。
- [ ] 在 `models.py` 增加两个模型与唯一约束；迁移创建 `trip_members`、`trip_invites`、令牌哈希唯一索引。
- [ ] 重跑同一测试，确认通过；提交 `Add trip collaboration persistence`。

### Task 2: 本地 API 与成员授权

**Files:**
- Modify: `backend/app/api/v1/trips.py`
- Create: `backend/app/api/v1/trip_invites.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/integration/test_trip_collaboration.py`
- Modify: `backend/tests/integration/test_ownership.py`

**Interfaces:** `require_trip_member(session, trip_id, user_id) -> Trip`；`GET /v1/trips/{trip_id}/members`；`POST /v1/trips/{trip_id}/invites`；`GET /v1/trip-invites/{token}`；`POST /v1/trip-invites/{token}/accept`。

- [ ] 写失败测试：拥有者创建邀请、同行人接受后可创建消费；第三人访问消费列表返回 404；已使用令牌再次接受返回冲突或失效。
- [ ] 运行 `uv run pytest backend/tests/integration/test_trip_collaboration.py -q`，确认路由缺失导致失败。
- [ ] 创建旅行时插入拥有者成员；将旅行、消费、结算的 `owner_id` 查询替换为成员授权；令牌由 `secrets.token_urlsafe(32)` 生成并只保存 SHA-256。接受邀请在事务内验证令牌、成员数小于 2、调用者尚未加入，然后插入成员与写入 `used_at`。
- [ ] 运行 `uv run pytest backend/tests/integration/test_trip_collaboration.py backend/tests/integration/test_ownership.py -q`，确认通过；提交 `Allow two members to edit a trip`。

### Task 3: CloudBase PostgreSQL 迁移与 RPC

**Files:**
- Create: `cloudbase/migrations/<YYYYMMDDHHMMSS>_add_trip_collaboration.sql`
- Modify: `backend/app/api/v1/trips.py`
- Modify: `backend/app/api/v1/trip_invites.py`
- Modify: `backend/tests/unit/test_cloudbase_migration_contract.py`

**Interfaces:** `tsb_create_trip` 同时写入首位成员；新增 `tsb_create_trip_invite` 和 `tsb_accept_trip_invite`，后者在单一事务中锁定邀请与成员。

- [ ] 写失败测试：迁移文本必须包含 `trip_members`、`trip_invites`、`used_at` 和 `tsb_accept_trip_invite`。
- [ ] 运行 `uv run pytest backend/tests/unit/test_cloudbase_migration_contract.py -q`，确认迁移缺失导致失败。
- [ ] 读取远端表结构和最后迁移版本，使用严格递增 UTC 时间戳创建迁移；SQL 创建两张表、索引和 RPC。接受 RPC 必须验证令牌、锁定旅行成员、确认少于两人、插入成员并写入 `used_at`。
- [ ] 重跑合约测试，确认通过；提交 `Add CloudBase trip collaboration RPCs`。

### Task 4: 小程序邀请与成员入口

**Files:**
- Modify: `miniprogram/services/api.js`
- Create: `miniprogram/pages/trip-invite/index.js`
- Create: `miniprogram/pages/trip-invite/index.wxml`
- Create: `miniprogram/pages/trip-invite/index.wxss`
- Create: `miniprogram/pages/trip-invite/index.json`
- Modify: `miniprogram/app.json`
- Modify: `miniprogram/pages/index/index.js`
- Modify: `miniprogram/pages/index/index.wxml`
- Modify: `miniprogram/pages/expense/index.js`
- Modify: `miniprogram/pages/expense/index.wxml`
- Create: `miniprogram/tests/trip-invite.integration.test.js`

**Interfaces:** `listTripMembers(tripId)`、`createTripInvite(tripId)`、`getTripInvite(token)`、`acceptTripInvite(token)`；加入页接收 `token`。

- [ ] 写失败测试：`acceptTripInvite("invite-token")` 发送 `POST /v1/trip-invites/invite-token/accept`。
- [ ] 运行 `node --test miniprogram/tests/trip-invite.integration.test.js`，确认 API 尚未导出导致失败。
- [ ] 实现服务封装和加入页：加载最小邀请摘要，接受成功后回到首页，失效/已使用/成员满时展示后端错误。首页存根和账单顶部显示成员数与成员入口；成员不足两人时创建邀请、复制邀请路径并支持微信转发。令牌不写入日志或本地持久化。
- [ ] 运行 `node --test miniprogram/tests/trip-invite.integration.test.js miniprogram/tests/home.integration.test.js`，确认通过；提交 `Add trip invite join flow`。

### Task 5: 紧凑账单录入与覆盖回归

**Files:**
- Modify: `miniprogram/pages/expense/index.js`
- Modify: `miniprogram/pages/expense/index.wxml`
- Modify: `miniprogram/pages/expense/index.wxss`
- Modify: `miniprogram/tests/expense-bill.integration.test.js`

**Interfaces:** `showBatchTools` 在 `selectedCount > 0` 时为真；`showAdjustments` 默认 `false`；`addItem`、`toggleItem`、`removeItem`、`saveAndPreview` 保持兼容。

- [ ] 写失败测试：初始 `showBatchTools`、`showAdjustments` 均为 `false`；`.checkout-bar` 的 CSS 块不匹配 `position: fixed`。
- [ ] 运行 `node --test miniprogram/tests/expense-bill.integration.test.js`，确认新增状态或布局钩子缺失导致失败。
- [ ] 商品改为两行连续列表（选择/名称/金额/删除，之后是分账 picker）；自定义比例才显示第三行。小票改为单行工具条；批量工具仅在选中商品时显示；调整项默认收起。减少垂直间距，保留墨绿、暖白、橙色票据风格；结算区保持文档流。
- [ ] 运行 `node --test miniprogram/tests/expense-bill.integration.test.js miniprogram/tests/ocr-review.integration.test.js`，确认通过；提交 `Compact the collaborative bill editor`。

### Task 6: 全量验证与 CloudBase 审查

**Files:**
- Modify: `docs/superpowers/specs/2026-09-19-collaborative-trips-compact-bill-design.md`（仅验收发现差异时）

- [ ] 运行 `node --test miniprogram/tests/*.test.js`、`uv run pytest backend/tests -q`、`uv run ruff check backend` 和 `uv run mypy backend/app`，均应通过。
- [ ] 审查迁移、RPC、授权和小程序：服务端才可用 API Key；成员授权不依赖 UI；迁移版本递增；邀请令牌不泄露。
- [ ] 应用迁移后，用两个测试微信账号完成创建邀请、接受、双方新增消费；第三账号访问失败。查询数据库确认两条成员记录及 `used_at`。
- [ ] 在微信开发者工具中添加至少三项商品，验证选择、添加、删除、分账和结算均可点击且无覆盖。
- [ ] 提交本设计与计划文档，且不暂存或覆盖用户的 `miniprogram/project.config.json`、`miniprogram/project.private.config.json` 改动。
