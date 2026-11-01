# 05 · 页面流转与状态

## 页面跳转

```mermaid
flowchart TD
  Launch([打开小程序]) --> Load{加载旅行}
  Load -- 失败 --> E06[06 异常首页]
  E06 -- 重试 --> Load
  E06 -- 仍要先新建旅行 --> N02b[02b 新建旅行]
  Load -- 没有旅行 --> H02a[02a 首页空状态]
  Load -- 有旅行 --> H01[01 首页]

  H01 -- 排序按钮 --> S01b[01b 排序与筛选弹层] --> H01
  H01 -- 新建旅行 --> N02b
  H02a -- 创建旅行 --> T03
  N02b -- 创建并邀请同行人 --> Share[[微信分享卡片]] --> T03[03 旅行详情]
  H01 -- 点旅行卡 --> T03
  H01 -- 待邀请卡 --> Share

  H01 -- + 记一笔 --> Cam[拍照/选图 · 未设计] --> OCR{识别}
  T03 -- + 记一笔 --> Cam
  OCR -- 成功 --> X05[05 记一笔]
  OCR -- 失败/手动 --> X05m[05 手动模式 · 未设计]
  X05 -- 更多选项 --> X05b[05b 详细]
  X05 -- 保存 --> Back([回到来源页])
  X05 -- 保存并继续 --> Cam

  T03 -- 点某天 --> D04a[04a 当天账单 · 待收款]
  T03 -- 合并结清 --> M[合并结清确认 · 未设计]
  T03 -- 管理 --> Set[旅行设置 · 未设计]
  D04a -- 确认已收款 --> D04b[04b 已结清]
  D04b -- 撤销 + 二次确认 --> D04a
  D04a -- 微信提醒小周 --> Share
  D04a -- 点某笔消费 --> X05b
```

## 「某一天」的状态机（查看者 = 收款方）

```mermaid
stateDiagram-v2
  [*] --> 记录中: 当天
  记录中 --> 已结清: 日期结束且净额 = 0
  记录中 --> 待收款: 日期结束且净额 ≠ 0
  待收款 --> 对方已标记付款: 小周点「我已付款」
  对方已标记付款 --> 已结清: 我点「确认已收款」
  待收款 --> 已结清: 我直接「确认已收款」或合并结清
  已结清 --> 待收款: 任一方「撤销」（二次确认）
```

付款方视角对称：待收款 ↔ 待付款，「确认已收款」↔「我已付款」（未设计，见 06）。

## 数据模型（建议）

```ts
Trip     { id: uuid, name, startDate?, endDate?, settleCurrency: 'CNY', members: Member[2], createdAt }
Member   { id, nickname, avatarChar, isMe }
Expense  { id: uuid, tripId, date, storeName, currency: 'JPY', amountMinor: int,   // 外币最小单位整数
           rate: decimal, actualChargeCny?: decimal, payerId,
           splitMode: 'equal' | 'items' | 'custom',
           items?: Item[], customShares?: {memberId, amountMinor}[],
           adjustments?: Adjustment[], receiptImage?, ocrConfidence? }
Item     { name, qty, amountMinor: int, owner: 'equal' | memberId }
DaySettlement { tripId, date, status: 'recording'|'pending'|'payer_marked'|'settled',
                payerMarkedAt?, confirmedAt?, confirmedBy? }
```

派生值（每人份额、当天净额、旅行汇总、首页汇总）都按 `01-business-rules.md` 实时计算，不要存冗余金额。
