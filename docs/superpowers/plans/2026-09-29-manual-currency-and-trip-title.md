# 手动录入币种与旅行标题修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复旅行标题重复编码，并让手动消费可选择日元等已有币种且有明确保存主操作。

**Architecture:** 仅修改小程序页面状态与展示。旅行详情由已有旅行列表按 ID 取原始字段，录入页复用现有币种数组；保存仍调用既有 `saveAndPreview`。

**Tech Stack:** 微信小程序原生 WXML/JavaScript、Node 内置测试运行器。

## Global Constraints

- 不改后端、数据库和接口。
- 不新增依赖，不支持单张账单混合商品币种。
- 币种仅限已有的 CNY、JPY、USD、KRW。

---

### Task 1: 标题与币种状态

**Files:** `miniprogram/pages/trips/index.js`, `miniprogram/pages/expense/index.js`, `miniprogram/tests/expense-bill.integration.test.js`

- [ ] 写出会失败的测试：旅行跳转从原始列表记录取得中文标题；切换 JPY 后构建的商品金额使用 JPY。
- [ ] 运行 `node --test miniprogram/tests/expense-bill.integration.test.js`，确认失败。
- [ ] 最小实现：`openTrip` 按 ID 查找 `this.data.trips`；`onCurrency` 更新 `currency` 并刷新汇率。
- [ ] 重跑聚焦测试，确认通过。

### Task 2: 录入页展示与回归

**Files:** `miniprogram/pages/expense/index.wxml`, `miniprogram/tests/expense-bill.integration.test.js`

- [ ] 写出会失败的测试：WXML 具有消费币种选择器和“保存并查看分账”文案。
- [ ] 运行聚焦测试，确认失败。
- [ ] 最小实现：在商品区增加绑定 `onCurrency` 的选择器，替换按钮文案。
- [ ] 运行 `node --test miniprogram/tests/*.js`，确认全部通过。
- [ ] 提交、合并并推送。
