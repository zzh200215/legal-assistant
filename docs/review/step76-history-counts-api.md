# 第七十六步交付说明：首屏历史 count 接口

> 一句话：新增 `GET /legal/history-counts`，一次返回咨询/合同审查/文书三个 COUNT；前端三个 tab 共用同一个 query key，标题首屏就显示真实总数，不再等「加载更多」才知道 total。

- 日期：2026-10-30
- 后端：`services/legal/legal_workspace_service.py`、`api/legal/legal_api.py`
- 前端：`api/legalWorkspace.js`、`query/keys.js`、`composables/useLegalHistoryCounts.js`、三个工作台 composable/tab
- 测试：`tests/test_legal_workspace_case_sharing.py` +1；e2e 1 passed
- 无迁移；OpenAPI **369**（新增 1 条路径）

---

## 1. 为什么补

step71–75 之后，三个历史标题都优先用各自 `/history` 的 `total`，但首屏还没请求 `/history`：

- 标题只能显示「已加载 50 条」；
- 用户要点一次「加载更多」才知道总数；
- 合同审查在筛选后能拿到 total，但普通模式首屏仍不准。

## 2. 后端

```http
GET /api/legal/history-counts?case_id=1
```

返回：

```json
{ "consultations": 12, "contract_reviews": 8, "drafts": 5 }
```

- 有 `case_id`：先 `verify_case_access`，统计该案件下三类记录；
- 无 `case_id`：只统计本人三类记录；
- 与 `list_rows` 的可见性口径一致；
- 三个 `COUNT` 一次返回，避免三个 tab 各打一个接口。

## 3. 前端

新增 `useLegalHistoryCounts`：

```js
key: qk.legal.historyCounts(caseId)
fetcher: client.getLegalHistoryCounts(...)
staleTime: 30s
```

- 三个 tab 共用同一个 key，切换 tab 不重复请求；
- 咨询标题：`historyTotal ?? historyCounts.consultations ?? consultations.length`；
- 文书标题：`draftHistoryTotal ?? historyCounts.drafts ?? drafts.length`；
- 合同标题：`contractHistoryTotal ?? (无筛选时 historyCounts.contract_reviews) ?? filtered.length`；
- 新建咨询/合同/文书成功后 invalidate `history-counts`，计数随列表刷新；
- 筛选中的合同仍以筛选后的 `contractHistoryTotal` 为准，不被全量 count 覆盖。

## 4. 验证

后端：

- 1 条咨询 + 2 条合同 + 3 条文书；
- `case_id` 查询 → `{consultations:1, contract_reviews:2, drafts:3}`；
- 本人查询 → 咨询 2（含严格案件那一条）、合同 2、文书 3；
- 外部组织访问案件 → 403/404。

e2e：

- 工作台 mock 新增 `/legal/history-counts`；
- 原咨询/文书/合同带入、审核、导出记录断言保持。

构建：

- 入口 81.0 / 100 KB；
- 最大页面 chunk 85.3 / 130 KB；
- 总资产 1684.3 / 1700 KB（余 15.7 KB）。

全量门禁见 §5。

## 5. 门禁

- `scripts/check_all.py`：**10/10，12.9 分钟，exit 0**（本树最终状态）。
- 后端全量：**2471 passed / 4 skipped**；OpenAPI **369**；前端单测 **96 passed**。
- 包体：入口 **81.0 / 100 KB**、最大页面 chunk **85.3 / 130 KB**、总资产 **1684.3 / 1700 KB**。

## 6. 边界

1. **count 是全量，不带筛选**：合同审查筛选时仍用 `/history` 的 total；普通模式才用 count。
2. **count 与列表可能短暂不一致**：新建后靠 invalidate 刷新，最多有一个请求周期延迟。
3. **多一个请求**：工作台进入时多打一次 `/history-counts`；三个 tab 共用，但非工作台页面不调用。
4. **只统计三类**：任务、通知等不在内。
5. **按 id 分页不影响 count**：count 是当前可见性下的全量。
6. **无新表/迁移**：一条只读聚合接口。

## 7. 后续建议

- 如果 count 查询在数据量大时变慢，可加覆盖索引或缓存；
- 余量 15.7 KB，下一步继续量产物。
