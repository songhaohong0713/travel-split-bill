# 核心记账流程前端重设计 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** 将旅行详情、消费录入和结算页改造成紧凑的旅行账本流程，同时不改变既有 API、OCR 或二人分账数据。

**Architecture:** 保持微信小程序原生 Page、WXML 和 WXSS 架构。旅行详情继续从 listExpenses() 生成消费摘要；消费页用现有 items 与 expandedItemId 管理账本行编辑；结算页只改信息层级和文案，不触碰发布、分享逻辑。

**Tech Stack:** 微信小程序 WXML/WXSS/JavaScript，Node 内置 node:test。

## Global Constraints

- 不增加依赖、不改后端接口、不变更页面路由。
- 使用 #124954、#143E47、#F6F4EC、#E8F0EB、#D06A42、#718186 作为核心视觉令牌。
- 旅行详情以一次消费为卡片；消费页商品必须在单一连续清单中展示。
- 默认不显示商品选择开关；仅点击“批量编辑”后显示选择控件。
- 自动识别、手动录入、二人分账、结算发布和分享链接必须保持可用。

---

## 文件结构

- miniprogram/pages/trip-detail/index.js、index.wxml、index.wxss：旅行消费摘要与小票优先录入入口。
- miniprogram/pages/expense/index.js：显式批量编辑状态。
- miniprogram/pages/expense/index.wxml、index.wxss：小票优先入口、账本行与小计。
- miniprogram/pages/settlement/index.wxml、index.wxss：结算单层级。
- miniprogram/tests/expense-bill.integration.test.js、miniprogram/tests/trip-detail.integration.test.js：页面回归测试。

### Task 1: 旅行详情改为紧凑消费卡片流

**Files:**
- Modify: miniprogram/pages/trip-detail/index.wxml
- Modify: miniprogram/pages/trip-detail/index.wxss
- Modify: miniprogram/pages/trip-detail/index.js
- Modify: miniprogram/tests/trip-detail.integration.test.js

**Interfaces:**
- Consumes: expenses[] 的 id, occurredAt, title, payer, itemCount, total, currency。
- Produces: 每项保留 data-id 与 bindtap="openExpense"；createExpenseRecord 接收用户选择的小票临时路径并传入消费页。

- [ ] **Step 1: 写失败测试**

~~~js
test("trip detail exposes compact expense summaries and a receipt-first action", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "..", "pages", "trip-detail", "index.wxml"), "utf8")
  assert.match(wxml, /class="expense-summary"/)
  assert.match(wxml, /class="expense-preview"/)
  assert.match(wxml, /拍小票，记录消费/)
})
~~~

- [ ] **Step 2: 确认测试失败**

Run: node --test miniprogram/tests/trip-detail.integration.test.js
Expected: FAIL，缺少 expense-summary 或“拍小票，记录消费”。

- [ ] **Step 3: 实施最小 WXML 结构**

~~~xml
<view wx:for="{{expenses}}" wx:key="id" class="expense-summary" data-id="{{item.id}}" bindtap="openExpense">
  <view class="summary-top"><text class="expense-title">{{item.title}}</text><text class="amount">¥{{item.total}}</text></view>
  <text class="expense-meta">{{item.occurredAt}} · {{item.itemCount}} 项 · {{item.payer || "待填写付款人"}}</text>
  <text class="expense-preview">查看商品与分账 ›</text>
</view>
~~~

将主按钮文案替换成“拍小票，记录消费”，邀请按钮不变。

- [ ] **Step 4: 让主按钮先选择小票，再创建消费**

将 createExpenseRecord 改为先调用 wx.chooseMedia({ count: 1, mediaType: ["image"], sourceType: ["camera", "album"] })；用户取消时直接结束。选择成功后，创建空消费记录，并把 tempFiles[0].tempFilePath 通过 receiptPath 查询参数传给消费页：

~~~js
wx.navigateTo({ url: `/pages/expense/index?tripId=${encodeURIComponent(this.data.tripId)}&currency=${encodeURIComponent(this.data.currency)}&expenseId=${encodeURIComponent(record.id)}&receiptPath=${encodeURIComponent(receiptPath)}` })
~~~

消费页 onLoad 读取 receiptPath，设置 receiptPath 和“等待上传识别”，再调用现有 startOcr(receiptPath)。保留一个次级“手动录入”按钮，调用现有的空消费创建流程，不传 receiptPath。

- [ ] **Step 5: 实施最小 WXSS**

页面底色设为 #F6F4EC。expense-list 保持一个纸面容器；expense-summary 只使用细分隔线，不用独立阴影卡。金额用 #D06A42，expense-preview 用 #718186 与 22rpx；主按钮用 #124954。

- [ ] **Step 6: 验证并提交**

Run: node --test miniprogram/tests/trip-detail.integration.test.js
Expected: PASS。

~~~bash
git add miniprogram/pages/trip-detail/index.wxml miniprogram/pages/trip-detail/index.wxss miniprogram/tests/trip-detail.integration.test.js
git commit -m "feat: redesign trip expense summaries"
~~~

### Task 2: 消费页建立显式批量编辑状态

**Files:**
- Modify: miniprogram/pages/expense/index.js
- Modify: miniprogram/tests/expense-bill.integration.test.js

**Interfaces:**
- Consumes: 每个 items[] 条目的 selected 与现有 applyBatchAllocation()。
- Produces: batchEditing: boolean 与 toggleBatchEditing()；showBatchTools 由 batchEditing 控制。

- [ ] **Step 1: 写失败测试**

~~~js
test("batch selection stays hidden until explicit batch editing begins", () => {
  const { definition } = loadExpensePage({}, { showToast() {} })
  const instance = pageInstance(definition, { items: [{ id: "a", selected: true, amount: "20" }] })
  assert.equal(instance.data.batchEditing, false)
  assert.equal(instance.data.showBatchTools, false)
  instance.toggleBatchEditing()
  assert.equal(instance.data.batchEditing, true)
  assert.equal(instance.data.showBatchTools, true)
})
~~~

- [ ] **Step 2: 确认测试失败**

Run: node --test miniprogram/tests/expense-bill.integration.test.js
Expected: FAIL，batchEditing 或 toggleBatchEditing 不存在。

- [ ] **Step 3: 实施最小状态代码**

在 data 增加 batchEditing: false；syncBill(changes) 把 showBatchTools 设为 Boolean(next.batchEditing)。新增：

~~~js
toggleBatchEditing() {
  const batchEditing = !this.data.batchEditing
  this.syncBill({
    batchEditing,
    items: this.data.items.map((item) => ({ ...item, selected: batchEditing ? item.selected : false })),
  })
}
~~~

不修改 toggleItem() 或 applyBatchAllocation() 的分账计算。

- [ ] **Step 4: 验证并提交**

Run: node --test miniprogram/tests/expense-bill.integration.test.js
Expected: PASS，新测试和既有批量分账测试都通过。

~~~bash
git add miniprogram/pages/expense/index.js miniprogram/tests/expense-bill.integration.test.js
git commit -m "feat: require explicit batch editing"
~~~

### Task 3: 消费页改为连续账本行与小票优先录入

**Files:**
- Modify: miniprogram/pages/expense/index.wxml
- Modify: miniprogram/pages/expense/index.wxss
- Modify: miniprogram/tests/expense-bill.integration.test.js

**Interfaces:**
- Consumes: batchEditing, expandedItemId, items, billTotal, currency, ocrStatus。
- Produces: 行继续使用 bindtap="toggleItemDetail"；编辑器继续使用所有 onItem*、removeItem 事件。

- [ ] **Step 1: 写失败测试**

~~~js
test("expense page renders items as one dense ledger and exposes the subtotal", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "..", "pages", "expense", "index.wxml"), "utf8")
  assert.match(wxml, /class="item-ledger"/)
  assert.match(wxml, /class="ledger-row"/)
  assert.match(wxml, /本次消费小计/)
  assert.match(wxml, /toggleBatchEditing/)
})
~~~

- [ ] **Step 2: 确认测试失败**

Run: node --test miniprogram/tests/expense-bill.integration.test.js
Expected: FAIL，缺少账本行结构或小计。

- [ ] **Step 3: 实施 WXML**

在 ticket-list 内以 item-ledger 包住循环，普通 ledger-row 只显示名称、金额、分配文字、箭头。选择开关只能在批量模式显示：

~~~xml
<switch wx:if="{{batchEditing}}" checked="{{item.selected}}" data-id="{{item.id}}" catchchange="toggleItem" color="#124954"/>
~~~

标题右侧加入：

~~~xml
<text class="batch-toggle" bindtap="toggleBatchEditing">{{batchEditing ? "完成批量编辑" : "批量编辑"}}</text>
~~~

保留 wx:if="{{expandedItemId === item.id}}" 的 item-editor、catchtap="stopItemEditorTap"、名称/金额输入、分配 picker、自定义比例和删除按钮。添加商品后加入：

~~~xml
<view class="expense-subtotal"><text>本次消费小计</text><text>¥{{billTotal}}</text><text>{{currency}}</text></view>
~~~

票据按钮文案改为“拍小票，记录消费”，继续绑定 chooseReceipt。

- [ ] **Step 4: 实施 WXSS**

删除 ticket-card.is-selected 的整卡背景。item-ledger 是单一 #FFFDF8 容器；ledger-row 用 2rpx 分隔线与 18rpx 垂直内边距；item-editor 用轻量 #E8F0EB 背景。expense-subtotal 使用 #124954、白字和 tabular-nums。checkout-bar 不能设置 position: fixed。

- [ ] **Step 5: 验证并提交**

Run: node --test miniprogram/tests/expense-bill.integration.test.js
Expected: PASS，OCR 自动填充、币种切换、编辑器 catchtap 和非固定底栏测试都通过。

~~~bash
git add miniprogram/pages/expense/index.wxml miniprogram/pages/expense/index.wxss miniprogram/tests/expense-bill.integration.test.js
git commit -m "feat: redesign expense ledger editor"
~~~

### Task 4: 结算页收紧为独立结算单

**Files:**
- Modify: miniprogram/pages/settlement/index.wxml
- Modify: miniprogram/pages/settlement/index.wxss
- Modify: miniprogram/tests/expense-bill.integration.test.js

**Interfaces:**
- Consumes: transfers, published, expiryDays, shareUrl 与现有 publish、share、copyShareLink。
- Produces: API 与交互不变，只调整结构和视觉层级。

- [ ] **Step 1: 写失败测试**

~~~js
test("settlement page keeps publishing and sharing in a distinct settlement sheet", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "..", "pages", "settlement", "index.wxml"), "utf8")
  assert.match(wxml, /class="settlement-summary"/)
  assert.match(wxml, /确认并发布/)
  assert.match(wxml, /创建并复制链接/)
})
~~~

- [ ] **Step 2: 确认测试失败**

Run: node --test miniprogram/tests/expense-bill.integration.test.js
Expected: FAIL，缺少 settlement-summary 或“确认并发布”。

- [ ] **Step 3: 实施结构与样式**

用 settlement-summary 包住转账列表或“已经结清”状态。发布区标题改为“确认并发布”，继续使用 bindtap="publish"。分享按钮继续使用 bindtap="share"；复制继续使用 bindtap="copyShareLink"。页面底色用 #F6F4EC，转账金额用 #D06A42，已结清用 #E8F0EB，发布按钮用 #124954。将多个相同圆角卡收敛为“结算摘要”和“后续操作”两层。

- [ ] **Step 4: 验证并提交**

Run: node --test miniprogram/tests/expense-bill.integration.test.js
Expected: PASS，既有“已经结清”和分享钩子测试继续通过。

~~~bash
git add miniprogram/pages/settlement/index.wxml miniprogram/pages/settlement/index.wxss miniprogram/tests/expense-bill.integration.test.js
git commit -m "feat: refine settlement sheet hierarchy"
~~~

### Task 5: 全量验证与开发者工具视觉检查

**Files:**
- No source changes expected.

- [ ] **Step 1: 运行前端全量测试**

Run: node --test miniprogram/tests/*.test.js
Expected: PASS。

- [ ] **Step 2: 运行后端回归测试与静态检查**

Run: uv run --project backend pytest backend/tests; uv run --project backend ruff check backend/app backend/tests
Expected: 后端测试通过，Ruff 输出 All checks passed!。

- [ ] **Step 3: 微信开发者工具检查**

重新编译后验证：可从旅行详情创建消费；拍照识别可填入多项商品；8 项商品仍是连续账本行；点任一项可编辑；仅批量编辑时显示选择；结算可发布并创建链接。

- [ ] **Step 4: 提交验证结果**

Run: git status --short; git log --oneline -5
Expected: 无意外源码变更；不要将开发者工具的 project.config.json 或 project.private.config.json 纳入功能提交。

## 自检

- 规格中的消费摘要、账本行、识别入口、批量模式、结算隔离和验收，分别由任务 1、3、3、2、4、5 覆盖。
- 未引入依赖、未修改 API 或数据模型。
- 所有行为改动均先有失败测试，后以最小代码实现。

