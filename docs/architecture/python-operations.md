# Python 服务部署与运维

本文说明自动分账 MVP 的首个线上服务：CloudBase 云托管中的 FastAPI API。当前 CloudBase 环境使用 **PostgreSQL 17.11**；本文不再使用 MySQL 连接串或 MySQL 相关配置。

## 当前可部署范围

| 组件 | 当前状态 | 责任 |
| --- | --- | --- |
| `travel-split-api` | 可作为首个云托管服务部署 | 微信登录、旅行/消费、结算、发布、分享、上传/OCR job API |
| CloudBase PostgreSQL | 由 CloudBase 环境提供 | 业务数据、结算快照、上传记录、OCR job 状态 |
| 私有 COS | 已单独创建 | 小票原图；服务端负责受限上传 URL 和后续读取 |
| 腾讯云 OCR | 需后续配置密钥 | 日语、英语、韩语优先的票据文字识别；结果必须人工核对 |
| OCR worker | **暂不部署** | 仓库尚没有 `app.worker.run` 入口，也没有生产 COS 图片加载器；不能先创建一个会持续失败的 worker 服务 |

API 与未来 OCR worker 最终会共享 PostgreSQL 和 COS，但当前仅部署 API。OCR job 的领取、真实 COS 读取和 worker 入口实现完成并验收后，才新增第二个服务。

## Docker 镜像与端口

镜像定义在仓库的 [`backend/Dockerfile`](../../backend/Dockerfile)。它运行：

```sh
uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
```

容器服务端口为 **8080**，CloudBase 页面中的访问端口可映射为 **80**。健康检查使用 `GET /healthz`，预期 HTTP 200：

```json
{"status":"ok"}
```

本地有 Docker 时可构建并检查：

```sh
docker build -f backend/Dockerfile -t travel-split-api:local .
docker run --rm -e PORT=8080 -p 8080:8080 travel-split-api:local
# 新终端：curl http://localhost:8080/healthz
```

## CloudBase Git 平台部署

在“云函数 / 托管 → 服务管理 → 新建 Git 平台部署”中填入：

| 控制台字段 | 值 |
| --- | --- |
| Git 仓库 | `songhaohong0713/travel-split-bill` |
| 部署分支 | 首次选择包含本配置的分支；验收后生产使用受保护的 `main` |
| 服务名称 | `travel-split-api` |
| 服务端口 | `8080` |
| 访问端口 | `80` |
| Dockerfile 路径 | `backend/Dockerfile` |
| 构建上下文 / 根目录 | 仓库根目录 `.` |
| 健康检查 | 路径 `/healthz`，端口 `8080` |

首次部署先关闭“自动部署”。确认镜像构建、迁移和 `/healthz` 都成功后，再按需要开启指定分支推送后的自动部署。

## Secret 与 PostgreSQL

所有密钥放在 CloudBase Secret，不能写入 Git、Docker 镜像层、日志或小程序包。`infra/cloudbase/cloudrun.yaml` 仅保存变量名称。

| 环境变量 | 用途 |
| --- | --- |
| `CLOUDBASE_ENV_ID` | CloudBase 环境 ID，例如 `travel-split-bill` |
| `CLOUDBASE_API_KEY` | 控制台“环境管理 → API 密钥”创建的 `api_key`；仅服务端 Secret 保存 |
| `JWT_SECRET` | access token 签名密钥，使用随机高强度值 |
| `WECHAT_APP_ID` / `WECHAT_APP_SECRET` | 微信 `code2session` 配置 |
| `COS_SECRET_ID` / `COS_SECRET_KEY` | 服务端访问私有 COS；后续应迁移为 CAM 临时凭证/最小权限角色 |
| `COS_BUCKET` / `COS_REGION` | `travel-split-bill-1486947970` 与 `ap-guangzhou` |
| `TENCENTCLOUD_SECRET_ID` / `TENCENTCLOUD_SECRET_KEY` | 腾讯 OCR SDK 配置 |
| `TENCENTCLOUD_REGION` | OCR 服务地域 |
| `OCR_MAX_ATTEMPTS` | OCR 最大尝试次数，默认 3 |
| `CORS_ORIGINS` | 分享 H5 或受信任前端来源；生产不可设为 `*` |

共享 PostgreSQL 不使用直连账号密码。服务通过 CloudBase PostgreSQL HTTP API 访问数据，因此必须在云托管变量中设置 `CLOUDBASE_ENV_ID` 与 `CLOUDBASE_API_KEY`；不要配置或猜测 `DATABASE_URL`。

`backend/Dockerfile` 在运行镜像中安装 `psycopg`，因此 DSN 必须使用 `postgresql+psycopg://`，不能使用 `mysql+pymysql://`。

## 首次迁移与发布顺序

1. 在 CloudBase PostgreSQL 的 SQL 编辑器执行 `infra/cloudbase/cloudbase_http_api_migration.sql`，保留成功记录。
2. 在云托管当前版本环境变量设置 `CLOUDBASE_ENV_ID`、`CLOUDBASE_API_KEY`、`JWT_SECRET`、`WECHAT_APP_ID`、`WECHAT_APP_SECRET` 和 `PORT=8080`。
3. 使用上表部署 `travel-split-api`，确认 CloudBase 日志无数据库/环境变量异常。
4. 请求服务公开 HTTPS 地址的 `/healthz`，确认 HTTP 200 和 `{"status":"ok"}`。
5. 再配置小程序合法 request 域名；COS 桶维持私有，不需配置 CDN 或公开读。

## 观测、安全与故障处理

- PostgreSQL、COS、OCR 仅由服务端访问；小程序永远不接触数据库或腾讯密钥。
- COS 上传 URL 应限定单对象、短期和 Content-Type；公开分享默认不附带小票原图。
- OCR `needs_review` 才进入人工核对；失败保留错误码并允许重新提交或手动录入。
- 发布结算使用不可变版本；分享链接只展示最新已发布版本，不展示草稿。
- API 监控 API 5xx、P95 延迟、数据库连接池耗尽、COS 上传确认失败和 OCR provider 超时/限流。
- 未来 worker 监控 `queued` job 年龄及 OCR 成功/失败/重试比例；在其入口和 COS 读取实现前，此项不适用。

## 本地验收

```sh
uv run pytest backend/tests -q
uv run ruff check backend
uv run mypy backend
```

真实云服务验收须使用脱敏小票，覆盖上传确认、OCR 成功/失败重试、人工核对、结算发布、链接过期和越权访问。