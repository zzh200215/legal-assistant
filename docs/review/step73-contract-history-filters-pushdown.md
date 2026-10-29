# 第七十三步交付说明：合同审查历史筛选下推

> 一句话：`GET /legal/contract-reviews/history` 新增 `status` / `risk_level` 参数，筛选在数据库完成；前端改筛选条件时重新拉第一页并进入「服务端筛选模式」，加载更多沿用同一组筛选。

- 日期：2026-10-29
- 后端：`services/legal/legal_workspace_service.py`、`api/legal/legal_api.py`
- 前端：`composables/useContractReviews.js`、`components/legal/LegalContractTab.vue`
- 测试：`tests/test_legal_workspace_case_sharing.py` 扩展筛选/分页断言；e2e 1 passed
- 无迁移；OpenAPI 仍 **368**（同一路径新增 query 参数）

---

## 1. 为什么补

step72 的合同审查「加载更多」只分页，不筛选：

- 页面上的状态/风险筛选只作用于**已加载的 50 条**；
- 第 51 条之后符合筛选的记录永远看不到；
- 用户选「律师通过」却只看到前 50 条里的几条，计数也不对。

## 2. 后端

```http
GET /api/legal/contract-reviews/history?status=lawyer_approved&risk_level=high&limit=20
```

- `status`：精确匹配 `ContractReview.status`；
- `risk_level`：`high|medium|low`，匹配 `risks_json` 中任一风险的 `risk_level`；
- 两个条件可组合；
- 先筛选再 `COUNT`，所以 `total/has_more` 是筛选后的口径；
- 继续用 `id desc` + `cursor` keyset。

风险匹配用 `LIKE` 兼容 MySQL/SQLite：

```sql
risks_json LIKE '%"risk_level": "high"%'
OR risks_json LIKE '%"risk_level":"high"%'
```

> 这是 JSON 文本匹配，不是 JSON 函数；对当前 `json.dumps(..., ensure_ascii=False)` 的落库格式有效，风险数组结构变化时需要同步调整。

## 3. 前端

`useContractReviews` 增加「服务端筛选模式」：

- 监听 `reviewFilter.status/risk`；
- 有任一筛选时：
  - `serverFiltered = true`，清空旧分页；
  - 调 `/history?status=&risk_level=&limit=20` 拉第一页；
  - 后续「加载更多」带同一组筛选；
- 清空筛选时回到普通列表（最近 50 条 + 更早分页）；
- 筛选请求期间 `historyStatus=loading`；
- 审核动作/重试仍可重新拉取（在筛选模式下会按当前筛选重拉）。

## 4. 验证

后端：

- 25 条合同，5 条律师通过、7 条含 high 风险；
- `risk_level=high` → total 7，且每条 risks 都含 high；
- `status=lawyer_approved` → total 5；
- 两条件组合 → total 5；
- `risk_level=high&limit=3` → 3 + has_more，下一页 3，再下一页 1 + 无更多。

e2e：

- 原合同审查带入/版本/审核断言保持；
- 少于 50 条时不显示加载更多。

构建：

- 入口 80.9 / 100 KB；
- 最大页面 chunk 85.3 / 130 KB；
- 总资产 1683.0 / 1700 KB（余 17.0 KB）。

全量门禁见 §5。

## 5. 门禁

- `scripts/check_all.py`：**10/10，15.7 分钟，exit 0**（本树最终状态）。
- 后端全量：**2470 passed / 4 skipped**；OpenAPI **368**；前端单测 **96 passed**。
- 包体：入口 **80.9 / 100 KB**、最大页面 chunk **85.3 / 130 KB**、总资产 **1683.0 / 1700 KB**。

## 6. 边界

1. **风险筛选是 JSON 文本 LIKE**：依赖 `risk_level` 字段名与引号格式；字段改名/压缩格式变化会漏匹配。
2. **`critical` 不算 `high`**：前端只提供 high/medium/low，后端也按精确值匹配；critical 记录选 high 不会出现。
3. **状态值未做枚举校验**：`status` 只限制长度，传错值返回空列表而不是 422。
4. **风险筛选无索引**：`risks_json LIKE` 无法用普通索引，合同量很大时会慢。
5. **筛选后 total 正确，但标题计数仍显示当前已加载数**：加载更多按钮/总数可继续完善。
6. **筛选状态与普通列表是两套数据源**：清空筛选会重新拉普通列表，cursor 不共享。
7. **前端仍保留客户端筛选**：服务端已筛过，客户端 `filteredContractReviews` 再做一次同样条件，结果不变但多一层计算。
8. **只下推合同审查**：咨询/文书历史没有等价筛选参数。

## 7. 后续建议

- 若合同量继续增长，把 `risks_json` 的筛选改成生成列/关联表并建索引；
- 标题计数改用 `contractHistoryTotal`，让筛选后的总数更直观；
- 余量 17.0 KB，下一步继续量产物。
