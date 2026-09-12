# Python 服务部署与运维

本文说明自动分账 MVP 的生产运行方式。API 与 OCR worker 使用同一 Python 3.12 镜像，但在 CloudBase Run 中作为两个独立服务运行：API 负责 HTTPS 请求，worker 负责从 MySQL job 表领取并处理小票 OCR 任务。

## 组件

| 组件 | 运行方式 | 责任 |
| --- | --- | --- |
| `split-api` | CloudBase Run，`uvicorn app.main:app` | 微信登录、旅行/消费、结算、发布和分享 API |
| `split-ocr-worker` | CloudBase Run，`python -m app.worker.run` | 领取 `receipt_jobs`、读取私有 COS、调用腾讯 OCR、写回候选和状态 |
| CloudBase MySQL | 私有网络优先 | 业务数据、结算快照、上传记录和 OCR job 状态 |
| 私有 COS | 禁止公共读 | 小票原图；API 只签发短期上传 URL，分享接口不返回原图 URL |
| 腾讯云 OCR | 服务端 SDK | 日语、英语、韩语优先的票据文字识别；结果必须人工核对 |

API 与 worker 必须使用同一镜像版本和同一个数据库。worker 可以水平扩容；job 领取必须依赖数据库行锁/状态条件，避免同一 job 被重复处理。

## 镜像与启动命令

在仓库根目录构建镜像（示例）：

```sh
docker build -f backend/Dockerfile -t <registry>/travel-split:<git-sha> .
docker push <registry>/travel-split:<git-sha>
```

API 服务命令：

```sh
uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
```

worker 服务命令：

```sh
python -m app.worker.run
```

worker 需实现优雅退出；收到 SIGTERM 后停止领取新任务，并等待当前 OCR 调用在超时内结束。生产环境不可使用内存队列替代 `receipt_jobs`。

## 必需 Secret 与环境变量

所有密钥放 CloudBase Secret，不写入镜像、Git、日志或小程序包。`infra/cloudbase/cloudrun.yaml` 只列出 Secret 名称和键名。

| 环境变量 | 用途 |
| --- | --- |
| `DATABASE_URL` | MySQL SQLAlchemy 连接串（含 TLS/连接池参数） |
| `JWT_SECRET` | access token 签名密钥 |
| `WECHAT_APP_ID` / `WECHAT_APP_SECRET` | 微信 `code2session` |
| `COS_SECRET_ID` / `COS_SECRET_KEY` | 服务端签发 COS URL；建议改为 CAM 临时凭证 |
| `COS_BUCKET` / `COS_REGION` | 私有小票桶 |
| `TENCENTCLOUD_SECRET_ID` / `TENCENTCLOUD_SECRET_KEY` | 腾讯 OCR SDK |
| `TENCENTCLOUD_REGION` | OCR 服务地域 |
| `OCR_MAX_ATTEMPTS` | OCR 最大尝试次数，默认 3 |
| `CORS_ORIGINS` | 分享 H5 或受信任前端来源，生产禁止 `*` |

参考汇率服务（如启用）应通过 `RATES_BASE_URL` 配置；外部服务超时和失败必须回退为人工录入/手动汇率。

## 首次部署与迁移

1. 创建 CloudBase MySQL、私有 COS 桶和 CloudBase Run 环境。
2. 创建 Secret，并按上表注入 API 与 worker。生产 Secret 不要复制到本地文件。
3. 先执行一次数据库迁移，再扩容 API/worker：

   ```sh
   cd backend
   alembic upgrade head
   ```

   迁移应使用与 API 相同的 `DATABASE_URL`。迁移命令是一次性运维 job，不应随每个 API 容器启动重复执行。
4. 部署 API，确认 `/healthz` 返回 `{"status":"ok"}`，再部署 worker。
5. 将小程序合法 request/upload 域名指向 API 与 COS 的 HTTPS 域名；COS 桶保持私有。

## 健康检查与观测

API liveness/readiness 使用 `GET /healthz`，HTTP 200 且 JSON `status=ok` 才算健康。CloudBase Run 探针建议初始延迟 5 秒、周期 10 秒、超时 3 秒、失败阈值 3 次。

worker 没有对外业务接口，至少输出结构化日志：`job_id`、`attempts`、`status`、`provider`、耗时和错误码；不得输出图片内容、OCR 完整原文、微信 openid 或任何 Secret。应监控：

- API 5xx、P95 延迟、数据库连接池耗尽；
- `queued` job 年龄、OCR 成功/失败/重试比例；
- COS 上传确认失败、OCR provider 超时/限流；
- 分享链接过期/撤销后的访问拒绝率。

告警阈值沿用产品方案：OCR/翻译 15 分钟失败率超过 10%、汇率失败率超过 5%、API P95 超过 8 秒或分享 API 5xx 超过 1%。

## 安全与故障处理

- MySQL、COS 和 OCR 只由服务端访问；小程序永远不接触数据库或腾讯密钥。
- COS 只接受短期、单对象、限定 Content-Type 的 PUT URL；公开分享默认不附带小票原图。
- OCR 结果状态为 `needs_review` 时才允许进入人工核对；OCR 失败保留图片和错误码，允许重新提交或手动录入。
- 发布结算使用不可变版本；分享链接只展示最新已发布版本，不展示草稿。
- 发生数据库故障时暂停 worker 领取任务；恢复后由 `queued`/可重试 job 继续处理。不要手工删除 job 记录来“清空队列”。

## 本地验收

```sh
uv run pytest backend/tests -q
uv run ruff check backend
uv run mypy backend
uv run alembic -c backend/alembic.ini upgrade head
```

真实云服务验收必须另行使用脱敏小票，覆盖上传确认、OCR 成功/失败重试、人工核对、结算发布、链接过期和越权访问。
