# 每日与当前旅行结清

## 已实现的规则

- 日期不会自动结束。任一旅行成员可手动结束当天记账。
- 按天结清和整段旅行结清共用同一份转账记录；旅行结清只选择已结束的日期。
- 各币种独立确认，跨天相反欠款可抵扣；净额为零时记录抵扣操作，无需真实转账。
- 历史转账保留。已付200、重新计算应付230时，剩余30；应付变180时形成反向退款20。
- 标记和撤销记录成员与UTC时间。撤销恢复各日期原有余额，不删除历史。
- 原结算版本发布/分享仍为旧版计算结果，不当作还款记录。
- 成员可编辑消费；删除仍为创建者可删除全部、受邀成员仅可删除自己创建的消费。邀请仍限创建者。

## 存储与一致性

`trip_ledgers` 每段旅行一行：`trip_id / revision / state_json`。state保存关闭日期、关闭操作人、转账与逐日有符号抵扣明细。JSON数据全部由服务端生成，前端不能指定转账金额。

一次转账保留计算所用消费快照。余额重算采用当前消费，减去未撤销的历史抵扣。已付款消费被删除后，退款差额仍有所属日期。

确认需要携带完整快照SHA256 token。来源消费、成员或账本版本变化后返回409 `LEDGER_CONFLICT`；客户端刷新并重新确认，不自动重复提交。

直接PostgreSQL路径锁旅行行；CloudBase路径使用RPC获取快照并比较/提交，消费写入触发器共同锁旅行行。账本表启用RLS，只允许服务端service_role，公开角色无直接读取或RPC执行权限。

## 新接口

| 方法与路径（均需JWT） | 用途 |
| --- | --- |
| GET /v1/trips/ledger-summary | 当前用户所有旅行待收与待付，分别汇总，不跨旅行抵扣 |
| GET /v1/trips/{id}/ledger | 每日状态、剩余应付、分摊及转账历史 |
| POST /v1/trips/{id}/days/{date}/close | 手动结束当天记账 |
| POST /v1/trips/{id}/payments/preview | `{dates:null}`选择所有已关闭日期；或传日期数组 |
| POST /v1/trips/{id}/payments | `{dates,token,currency}`标记转账/抵扣 |
| POST /v1/trips/{id}/payments/{payment_id}/revoke | 撤销并保留历史 |
| PATCH /v1/trips/{id} | 创建者更新`start_date/end_date`，null可清空 |

同一旅行有尚未补全的关闭日期时，整体结清不跳过错误日期，先修正消费；仍可单独结清其他有效日期。

## 本地验证

在仓库根目录运行：

```powershell
python -m pytest -q
node --test miniprogram/tests/*.test.js
python scripts/verify-miniprogram.py 'D:/微信web开发者工具/resources/app.asar.unpacked/node_modules/wcc-exec'
```

`scripts/verify-ledger-pg.mjs`使用临时PGlite真实PostgreSQL引擎验证SQL迁移、RPC版本冲突、消费变更失效和权限。将PGlite安装到临时目录，然后传其`dist/index.js`绝对路径，不需修改项目依赖或连接远端数据库。

## 发布前顺序

本次只准备代码与脚本，未执行远端迁移、部署或上传。

1. 核对目标环境现有schema和已安装迁移，确认已有trip_members及当前权限RPC。
2. CloudBase路径通过版本化迁移应用`infra/cloudbase/20261021_daily_settlement.sql`；直接数据库路径运行Alembic升级。两套是替代路径，不重复执行。
3. 部署后端，验证两位测试成员的读写、重复提交和撤销。
4. 用微信开发者工具打开本分支的`miniprogram`目录，验证真机安全区、键盘、字体、相册图片和双人同步；随后上传小程序。

旧旅行默认均处于记录中，旧发布结果不会自动变为已经转账。旅行起止日期不自动关闭每日账单。

## 验证边界

已做接口/前端行为测试、官方WXML/WXSS编译，以及临时PostgreSQL SQL验证。尚未运行真实微信模拟器/手机的端到端流程，也未在真实CloudBase环境执行迁移与并发压测；因此不能声称截图达到逐像素还原或线上已可用。
