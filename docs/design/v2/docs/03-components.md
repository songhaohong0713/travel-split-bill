# 03 · 组件

建议做成小程序自定义组件（`components/<name>/`）。数值见 `02-design-tokens.md`，具体图层见 `figma-outline/`。

## TripCard（Figma 组件 `Trip Card v2`，node 2014:80）

首页旅行列表项。整卡可点，进入 03 旅行详情（「待邀请」状态点击右侧时进入邀请）。

```
[首字色块 44]  标题 17 Bold  [进行中]        状态值
               日期 · 成员 13 secondary        状态说明 12 secondary
```

- 横向排列，间距 14；内边距 上下 18 / 左 18 / 右 20；白底；圆角 20；列表项间距 12。
- 属性：`title` 标题、`initial` 首字、`meta` 日期与成员、`value` 状态值、`label` 状态说明、`ongoing` 是否显示「进行中」、`state`。

| state | 首字色块 | 状态值 | 状态说明 |
|---|---|---|---|
| `receivable` 待收款 | 底 `brand/green-subtle`，字 `brand/green` | `¥320.00` 16 Bold `brand/green` | 小周待付你 |
| `invite` 待邀请 | 底 `status/pending-bg`，字 `status/pending` | 去邀请 14 Medium `status/pending` | 只有你一人 |
| `settled` 已结清 | 底 `bg/subtle`，字 `text/secondary` | 已结清 14 Medium `text/secondary` | 10/03 结清 |

- 「进行中」标签：内边距 2/8，圆角 10，底 `brand/green-subtle`，字 11 Medium `brand/green`，紧跟在标题后（间距 8）。
- 还需要补一个 `payable`（我待付）状态，样式参照待收款，金额改用 `status/pending` 琥珀色，说明「你待付小周」。

## DayCard（03 每日账单）

```
10月7日 周三 16 Bold                       [状态标签]
3 笔 · 9,971 JPY 13 secondary      小周应付 ¥235.97 14 Medium  >
```
- 竖向，间距 8，内边距 18，白底，圆角 20，卡片间距 16。整卡可点，进入 04。
- 状态标签见 `01-business-rules.md` 第 5 节；已结清时右侧文字为「小周已付 ¥43.80」，颜色 `text/secondary`。

## Button

| 变体 | 规格 |
|---|---|
| primary | 高 54，圆角 16，底 `text/primary`，字白 16 Medium |
| secondary | 高 54，圆角 16，白底，1px `bg/subtle` 描边，字 `text/primary` |
| disabled | 底 `bg/disabled`，字 `text/placeholder`，不可点 |
| small | 高 44，圆角 12，字 14 Medium；可选绿色变体（底 `brand/green-subtle`，字 `brand/green`，无描边） |
| text link | 13–14 Medium `brand/green`；危险链接用 `status/danger` + 下划线（如「撤销」） |

底部两个按钮并排时：次按钮固定宽（首页 110、记一笔 132），主按钮撑满，间距 10。

## StatusChip

胶囊。列表内 12 Medium、内边距 4/10；页头 13 Medium、内边距 6/12。颜色按状态：记录中（灰）/ 待收款·待付款·待邀请（琥珀）/ 已结清·进行中（绿）。

## Input / Field

- 字段 = 标签（12 Medium secondary）+ 6 + 输入框（48 高，`bg/page` 底，圆角 14，内边距 13/16，文字 15）+ 6 + 说明（12 Regular secondary，可选）。
- 必填写在标签里：「旅行名称（必填）」；可选同理：「出行日期（可选）」。
- 日期区间：两个输入框各占一半，中间「–」，间距 8，右侧日历图标 16。
- 下拉：右侧 chevron-down 16。
- 后缀单位：右侧文字 15 secondary（如「元」）。

## FieldRow（05 字段卡里的一行）

左：标签 14 Regular secondary；右：值 15 Medium + 可选附加信息 12 + chevron-down 14。行内边距 16/0，行间 1px `bg/subtle` 分隔线。

## DateChips

「今天 10/08」「昨天」「选日期」三个胶囊，间距 8；右上角说明「与小票日期一致」（12 `brand/green`），小票日期和所选日期不同时改成提示「小票日期为 10/07」。

## Segmented

外框高 40、内边距 3、底 `bg/page`、圆角 12；选中块白底圆角 10，字 14 Medium primary；未选中 14 Regular secondary。

## AmountDisplay（05 / 05b 金额卡）

左对齐三层：
1. 店名 16 Medium + 编辑图标 16（可点改名）
2. 金额 48 Bold + 币种胶囊（`JPY ▾`，13 Medium，`bg/page` 底），**基线对齐**，间距 10；金额可点编辑
3. 折算 13 secondary：「≈ ¥54.22 · 汇率 1 JPY = 0.04236 CNY」

## SplitSummary（05 分摊预览、05b 本笔分摊）

浅绿底（`brand/green-subtle`）、圆角 14、内边距 14、竖向间距 10：
- 每人一行：头像 28 + 名字 14 Medium（撑满）+ 右侧两行金额（外币 14 Bold / `≈ ¥` 12 secondary，右对齐）。付款人名字后加「（付款人）」。
- 1px `brand/green-border` 分隔线
- 结论句 12 Medium `brand/green`：「你付了全部 1,280 JPY，小周应还你 ¥27.11」

## ItemRow + OwnerTag（05b 商品明细）

```
商品名 15 Medium [归属标签]                 金额 15 Medium
数量 12 secondary
```
- 归属标签紧跟商品名（间距 8），点击循环切换 平分 → 我的 → 小周的；金额靠右、宽度自适应。
- 标签：内边距 2/8，圆角 10，11 Medium。平分：`bg/subtle` + `text/secondary`；我的：`text/primary` 底 + 白字；小周的：`brand/green-subtle` + `brand/green`。
- 行内边距 12/0，行间 1px `border/divider`；底部「+ 添加商品」（`brand/green`）与「合计 2,964 JPY」（14 Bold）。

## TransferRow（04 收款）

`bg/page` 底（已结清时 `brand/green-subtle`）、圆角 18、内边距 16：左侧「付款人头像 → 收款人头像」（36，间距 10），右侧金额 22 Bold + 「人民币」11 secondary。已结清时金额变 `brand/green`。头像在绿底上要用白底，保证可见。

## SettleTable（04 分摊明细）

三列：成员（撑满）/ 实际支付（元）/ 应承担（元），后两列右对齐、等宽数字；表头 12 secondary；数据 15，应承担一列 Bold。

## BottomBar

固定底部，渐变背景，可选上方一行说明（12 secondary，居中，与按钮间距 10）。见 `02-design-tokens.md` 第 6 节。

## BottomSheet（01b）

遮罩 + 白色弹层（顶部圆角 24，内边距 10/24/34/24）：把手 → 标题行（17 Bold + 右侧「重置」14 secondary）→ 分组标题（12 Medium secondary）→ 选项行（58 高：主文字 15 + 说明 12；选中项主文字 Medium `brand/green` + 右侧对勾 18）→ 开关行。点遮罩或下滑关闭。

## Switch

44×26，圆角 13；关：`bg/disabled` 底；开：`brand/green` 底；白色圆点 22，阴影 0 1 3 rgba(0,0,0,.15)。

## Notice（06 错误提示条）

`status/danger-bg` 底、圆角 16、内边距 14/12/14/16：标题 14 Medium + 说明 12，均为 `status/danger`；右侧白底胶囊按钮「重试」13 Medium。

## Skeleton

与 TripCard 同尺寸的白卡，内部是 `bg/subtle` 圆角 6 的灰条（16 高、12 高两行 + 右侧 56×14）。加载中可加轻微闪烁动画。

## Avatar

圆形。「我」：`text/primary` 底 + 白字；同行人：白底 + `brand/green` 字（放在白色背景上时用 `brand/green-subtle` 底）。文字取昵称最后一个字，Bold。
