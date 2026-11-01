# 给 Codex 的任务说明（可直接复制）

---

请按照仓库中 `travel-ledger-design-handoff/` 的设计交接包，实现「旅账」微信小程序的前端（原生小程序：WXML / WXSS / JS）。

**先读这些文件，按这个顺序：**
1. `README.md`：页面清单、Figma 节点、技术前提
2. `docs/01-business-rules.md`：币种显示、汇率换算、四舍五入、分摊、按天结算（最重要）
3. `docs/02-design-tokens.md` 和 `tokens/tokens.wxss`：颜色、字号、圆角、间距
4. `docs/03-components.md`：组件
5. `docs/04-screens.md` + `screens/*.png` + `figma-outline/*.txt`：每个页面
6. `docs/05-flows.md`：跳转、状态机、数据模型
7. `docs/06-open-questions.md`：不一致和未设计的部分，按其中「暂行做法」实现

**如果你已连接 Figma MCP**：设计文件在 README 里，只看「v2 · 走查修订版」区块（node 2004:23）。需要核对精确样式时，对单个页面节点调用 get_design_context / get_screenshot。Figma 返回的参考代码是 React + Tailwind，只用来读数值，不要照搬，要改写成小程序代码。

**要求：**
- 所有颜色、字号、圆角、间距都用 `tokens/tokens.wxss` 里的变量，不要写死其他数值。设计稿 1px = 2rpx。
- 金额逻辑全部放在 `utils/money.js`，用整数（外币最小单位 / 人民币的分）或 decimal 计算，不要用浮点数直接乘。按 `docs/01-business-rules.md` 第 7 节写单元测试，全部通过后再写页面。
- 「¥」只能用于人民币。外币写成「1,280 JPY」。
- 状态栏和右上角胶囊是系统自带的，使用原生导航栏。
- 先用本地 mock 数据把 01 → 03 → 04a/04b 和 05/05b 跑通，再接后端。mock 数据请复用截图里的样例（东京、府中、札幌；10月5日–8日；药妆店、小破化妆品店、DHC 油）。
- 设计稿里标注「未设计」的地方，按 `06-open-questions.md` 的暂行做法实现，并留 `TODO(design)` 注释。
- 每完成一个页面，截图和 `screens/` 里对应的图对比，列出差异。

**建议的目录：**
```
utils/money.js            汇率换算、分摊、按天结算（附测试）
styles/tokens.wxss        从 tokens/tokens.wxss 复制，在 app.wxss 里 @import
components/               trip-card, day-card, button, status-chip, field, date-chips,
                          segmented, amount-display, split-summary, item-row,
                          transfer-row, settle-table, bottom-bar, bottom-sheet, switch,
                          notice, skeleton, avatar
pages/home                01 / 01b / 02a / 06（同一页面的不同状态）
pages/trip-new            02b
pages/trip-detail         03
pages/day-bill            04a / 04b
pages/expense-edit        05 / 05b（同一页面，快速 / 详细两种展开状态）
```

---
