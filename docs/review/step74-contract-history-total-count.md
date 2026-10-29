# 第七十四步交付说明：合同审查标题计数改用 contractHistoryTotal

> 一句话：合同审查历史面板的「N 条」不再只数当前已加载/筛选结果，而是在 `/history` 返回 `total` 后直接用它；首个 20 条筛选页也能显示全库匹配总数。

- 日期：2026-10-29
- 前端：`components/legal/LegalContractTab.vue`
- 测试：e2e 1 passed；无后端/接口/迁移变化
- 包体：总资产 1683.1 / 1700 KB

---

## 1. 为什么补

step72/73 之后，合同审查历史有几层计数：

- `filteredContractReviews.length`：当前已加载集合再按状态/风险过滤；
- `contractHistoryTotal`：服务端筛选后的全库总数（`/history` 返回）。

标题一直用前者：

- 筛选「律师通过」时，当前页 20 条里可能只有几条，标题显示「2 条」；
- 实际全库 50 条匹配，用户会误以为只有 2 条；
- step73 已把 total 拿到，只是没用上。

## 2. 改法

`LegalContractTab.vue`：

```js
const contractHistoryCount = computed(() => (
  contractHistoryTotal.value !== null
    ? contractHistoryTotal.value
    : filteredContractReviews.value.length
))
```

- `/history` 返回 total 后，标题/折叠摘要统一用它；
- 还没请求过 `/history`（首屏普通列表、未加载更多）时，退回已加载数；
- 清空筛选重新拉普通列表时 `contractHistoryTotal` 归零，标题回到已加载数；
- 加载更多后 total 保持服务端口径。

使用位置：

- `<summary>历史审查 N 条</summary>`；
- 卡片头 `N 条`。

## 3. 效果

- 普通模式：首屏「50 条（还有更早）」；点加载更多后显示真实总数；
- 筛选模式：第一页就显示筛选后的全库总数，不再只看当前 20 条；
- 加载更多/清空筛选后计数随 total 更新。

## 4. 验证

e2e：

- 工作台 e2e 通过（合同审查面板渲染、带入、审核断言保持）；
- 未新增截图：计数变化在数据量 > 当前页时才明显。

构建：

- 入口 80.9 / 100 KB；
- 最大页面 chunk 85.3 / 130 KB；
- 总资产 1683.1 / 1700 KB（余 16.9 KB）。

全量门禁见 §5。

## 5. 门禁

- `scripts/check_all.py`：**10/10，14.1 分钟，exit 0**（本树最终状态）。
- 后端全量：**2470 passed / 4 skipped**；OpenAPI **368**；前端单测 **96 passed**。
- 包体：入口 **80.9 / 100 KB**、最大页面 chunk **85.3 / 130 KB**、总资产 **1683.1 / 1700 KB**。

## 6. 边界

1. **首屏未请求 total**：普通模式仍先显示已加载 50 条，加载更多后才准确。
2. **total 与已加载不同步**：显示「共 N 条」但列表可能只有当前页；用户需继续加载更多。
3. **筛选条件变化会重置 total**：请求期间标题短暂回落已加载数。
4. **客户端筛选仍参与兜底**：total 为空时用 `filteredContractReviews.length`，可能小于服务端总数。
5. **仅合同审查**：咨询/文书标题仍用各自已加载数。
6. **无接口/迁移变化**：纯展示层。

## 7. 后续建议

- 咨询/文书也接入类似 total（需要它们的 `/history` 返回值已在用，可同法处理）；
- 首屏普通列表可加 count 接口，让「共 N 条」一开始就准确；
- 余量 16.9 KB，下一步继续量产物。
