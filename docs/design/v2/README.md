# 旅账 · v2 设计交接包（给 Codex）

两人旅行记账、按天分账的微信小程序。本包是 Figma「v2 · 走查修订版」的完整交接材料：
**样式以 Figma 为准，意图、业务规则和状态以本包为准。** 两者冲突时，先看 `docs/06-open-questions.md` 有没有记录，没有就以本包为准并提出来。

## 1. 先连上 Figma（推荐）

Codex 可以通过 Figma 官方 MCP 服务器直接读取设计稿（图层、间距、颜色变量、组件、截图）。

- **Codex App**：左上角 Plugins → Figma 旁的 + → Install Figma → Allow access。
- **Codex CLI**：
  ```bash
  codex mcp add figma --url https://mcp.figma.com/mcp
  ```
  按提示登录 Figma 并授权。

注意：
- 用**有完整编辑席位（Full seat）的 Figma 账号**登录。免费 Starter 账号的 MCP 调用次数很少，读几次就会被限流。
- 该账号需要能打开下面这个文件。

**Figma 文件**：<https://www.figma.com/design/W3wC1moziqVL4GYNPW2N6m/%E6%97%85%E8%B4%A6-%C2%B7-%E9%87%8D%E6%96%B0%E8%AE%BE%E8%AE%A1--Copy-?node-id=2004-23>
只看 **「v2 · 走查修订版」区块（node 2004:23）**。画布上方的旧稿和标「已废弃」的页面都不要实现。

## 2. 页面清单

| # | 页面 | Figma node | 截图 | 结构大纲 |
|---|---|---|---|---|
| 01 | 首页 | `2004:24` | `screens/01-home.png` | `figma-outline/01-home.txt` |
| 01b | 首页 · 排序与筛选弹层 | `2015:54` | `screens/01b-home-sort-sheet.png` | `figma-outline/01b-home-sort-sheet.txt` |
| 02a | 首页（空状态） | `2004:67` | `screens/02a-home-empty.png` | `figma-outline/02a-home-empty.txt` |
| 02b | 新建旅行 | `2004:114` | `screens/02b-new-trip.png` | `figma-outline/02b-new-trip.txt` |
| 03 | 旅行详情（按天） | `2004:161` | `screens/03-trip-detail.png` | `figma-outline/03-trip-detail.txt` |
| 04a | 当天账单 · 待收款（我是收款方） | `2004:244` | `screens/04a-day-settle-pending.png` | `figma-outline/04a-day-settle-pending.txt` |
| 04b | 当天账单 · 已结清 | `2004:342` | `screens/04b-day-settled.png` | `figma-outline/04b-day-settled.txt` |
| 05 | 记一笔（确认） | `2004:439` | `screens/05-add-expense.png` | `figma-outline/05-add-expense.txt` |
| 05b | 记一笔（详细 · 按商品分摊） | `2016:85` | `screens/05b-add-expense-detailed.png` | `figma-outline/05b-add-expense-detailed.txt` |
| 06 | 异常 · 首页（登录未完成） | `2004:517` | `screens/06-error-login.png` | `figma-outline/06-error-login.txt` |
| — | 组件 Trip Card v2 | `2014:80` | `screens/component-trip-card-v2.png` | `docs/03-components.md` |

Figma 链接格式：把上面链接里的 `node-id=2004-23` 换成对应 node（冒号改成短横线），例如 `node-id=2004-24`。

## 3. 文件说明

```
README.md                  ← 本文件
CODEX_PROMPT.md            ← 可直接贴给 Codex 的任务说明
docs/
  01-business-rules.md     ← 币种、换算、四舍五入、分摊、结算状态（最重要，画面上看不出来）
  02-design-tokens.md      ← 颜色、字号、圆角、间距、尺寸规范
  03-components.md         ← 可复用组件及其状态
  04-screens.md            ← 每个页面的目的、入口、交互、状态、文案
  05-flows.md              ← 页面跳转与状态流转
  06-open-questions.md     ← 尚未设计或需要产品决定的点
tokens/
  tokens.json              ← 设计 token（JSON）
  tokens.wxss              ← 同一套 token 的小程序 CSS 变量
screens/                   ← 2x 截图（750 宽），与设计稿 375 宽一一对应
figma-outline/             ← 每页的图层结构、间距、token 名和全部文案（从 Figma 导出）
```

## 4. 技术前提（可改）

- 按**原生微信小程序**写。若改用 Taro / uni-app，只需改 `docs/02-design-tokens.md` 第 6 节和组件目录结构，规则不变。
- 设计稿宽 375，**1px = 2rpx**。截图是 2x，截图上的 1 像素 = 1rpx。
- 状态栏和右上角胶囊按钮是系统自带的，不要实现。导航栏用小程序原生导航栏（背景 `#F6F4F0`，黑色标题）。
- 字体：设计稿用 Noto Sans SC；实现用系统字体栈（见 tokens），金额用等宽数字。

## 5. 样例数据

截图里的金额是演示数据。以下几组已按 `docs/01-business-rules.md` 的规则核对过，可直接写成单元测试：
- 05：1,280 JPY × 0.04236 = ¥54.22；平分 640 / 640 JPY → 各 ¥27.11
- 05b：2,964 JPY = ¥125.56；按商品 我 1,858 / 小周 1,106 JPY → 小周 ¥46.85，我 ¥78.71
- 04a：当天 3 笔 1,280 + 2,964 + 5,727 JPY → 逐笔换算后合计 ¥422.38
- 03：整趟 19,561 JPY ≈ ¥828.60（逐笔换算后求和）
- 01 / 03：待收 ¥320.00 = 10月7日 ¥235.97 + 10月6日 ¥84.03

其他数字（如每天「小周应付」的具体金额）只是占位，不保证能从截图里的消费推算出来。
