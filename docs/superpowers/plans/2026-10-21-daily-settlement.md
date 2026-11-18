# 每日与旅行结清实施计划

目标：保留原生小程序、FastAPI/JWT/PostgreSQL/CloudBase，实现手动结束每日记账、按天或旅行结清、转账撤销和修改后的差额。

## 约束
- 用户已批准方案并授权实施；本地开发，不部署或修改线上数据。
- 保留现有删除、邀请和消费编辑权限。
- 金额由既有 Decimal 结算引擎计算，按币种独立；记录中的日期不纳入旅行结清。
- 旧发布版本不是还款记录，不迁移成已付款。

## 实施顺序
- [x] 测试：已转200后应付230只剩30、反向退款、跨天抵扣、撤销、版本冲突、成员权限。
- [x] 新增 trip_ledgers（旅行唯一、revision、state_json）。JSON 内保存关闭日期、转账历史及逐日抵扣明细；全部通过成员鉴权的原子写入，避免另建三套状态。
- [x] core/ledger.py：读取持久化消费，计算日结果、扣除有效转账，生成预览 token；转账记录保存原始每日余额及操作人时间。
- [x] api/v1/ledger.py：GET ledger、POST days/{date}/close、POST payments/preview、POST payments、POST payments/{id}/revoke。客户端仅提交范围和预览token，后端重算。
- [x] Alembic 和 CloudBase SQL 同步提供表及快照/CAS RPC；旅行删除级联清理。
- [x] 小程序 service、每日账单页面、旅行详情接入；增加首页后端汇总；按截图配色、卡片、分摊表和状态。
- [x] 日期字段、OCR商户/日期扩充及确认页逐步接入，保留手动录入。
- [x] 运行后端pytest、前端node测试、ruff；检查迁移与权限；明确真机和远端迁移未验证项。

## 数据契约
state_json = {closed_dates: [ISO日期], payments: [{id, created_at, created_by, currency, from_participant_id, to_participant_id, amount, allocations: [{date, amount}], revoked_at?, revoked_by?}]}。
allocations.amount 使用固定成员排序中第二人欠第一人的有符号金额，允许跨日相抵为零。金额采用字符串。关闭日期保留，即使当天消费删除；历史转账造成的反向退款仍可见。
预览 token 为 snapshot（旅行币种、成员、全部消费id/revision/payload、账本revision）的规范JSON SHA256。过期提交409，重复提交不重复入账。
同一旅行串行提交；CloudBase消费写入通过触发器与账本写入共同锁旅行，CAS核对完整消费快照。两位成员可结束当天、标记与撤销，撤销保留历史。

## 验收
按批准方案的八项场景验证。迁移部署前旧数据保持记录中，不根据日期自动关闭；旧前端发布功能保留但不作为还款入口。UI采用米白#F7F5F1、绿#2F5D50、正文#1D1D19，PingFang SC等设备字体，尊重截图布局并适配安全区。

## 完成验证（2026-10-22）
后端87项、前端89项通过；微信官方编译器编译7份WXML/9份WXSS；临时PGlite运行迁移及CAS/权限验证；独立审查的消费金额展示和过期预览两项问题均已修复并增加回归测试。未远端部署，未做模拟器/真机视觉验收，详见 docs/architecture/daily-settlement.md。
