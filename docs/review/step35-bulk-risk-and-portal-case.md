# 第三十五步交付说明：合同风险批量处置 + 门户案件概览

> 一句话：合同页可以勾选多条风险一次性「接受 / 缓释 / 忽略」，每条仍单独留处置记录；客户门户首屏多了「案件概览」，客户一进来就知道这是哪个案子、当前是什么状态。

- 日期：2026-10-23
- 后端：`app/services/legal/legal_domain_service.py`（`bulk_update_risk_item_status`）、`app/api/legal/legal_domain_api.py`（`POST /contract-reviews/{id}/risk-items/bulk-action`）、`app/api/legal/portal_links_access.py`（门户内容附 `case`）
- 前端：`LegalContractTab.vue`（勾选 + 批量操作条）、`RiskIssueItem` 所在列表行、`LegalPortal.vue`（案件概览卡）
- 测试：`tests/test_legal_domain_api.py`（+3 例）、`tests/test_legal_portal_aggregate.py`（+1 断言）、e2e `contract-upload`

---

## 1. 合同风险批量处置

### 1.1 接口

```
POST /api/legal/contract-reviews/{review_id}/risk-items/bulk-action
{ "risk_ids": [1, 2], "action": "accept|mitigate|dismiss|reopen", "note": "..." }
```

- 权限与单条处置一致：审核律师 / 管理员；
- **全有或全无**：任一下标不存在 → 404 `RISK_ITEM_NOT_FOUND`，整批不改；动作不合法 → 400，整批不改；
- 每条风险各写一条 `LegalReviewAction`（`risk_index` + `risk_title` + 前后状态），可逐条追溯；
- 返回更新后的风险项列表（带 `history`）。

### 1.2 前端

风险列表每行左侧出现复选框（仅审核角色可见），勾选后顶部出现批量条：`已选 N 项 · 接受 / 缓释 / 忽略 / 清除`。执行后按钮收起、提示「已批量处理 N 条风险」，详情里的处理状态即时更新。

## 2. 门户案件概览

- `GET /portal/{token}/content` 增加 `case` 字段：`{id, title, status, case_type, updated_at}`（仅当链接归属案件存在且同组织）；
- 客户门户内容页首屏新增「案件概览」卡：案件名、状态标签（进行中/已结案/已归档）、最近更新时间；
- 老链接（case_id 为空）不显示该卡，其余内容不变。

## 3. 验证

| 项 | 结果 |
| --- | --- |
| 后端批量 | 两条风险批量接受 → 200、状态 accepted、history 的 risk_index 0/1；批量 reopen → 回到待处理；含非法 id 的批量 → 404 且一条都没改；普通成员批量 → 403 |
| 门户 | 聚合链接内容返回 `case.title=聚合案件`、`status=in_progress`；其余聚合断言不变 |
| e2e | `contract-upload`：勾选两条 → 批量条显示「已选 2 项」→ 接受 → 请求体 `{risk_ids:[1,2], action:"accept", note:"合同审查工作区批量处理"}` → 批量条消失、详情显示「已接受」 |
| 契约 | openapi 快照重基线（新增 1 条路由，additive）；`types:check` 一致 |
| 门禁 | 见提交说明 |

## 4. 边界

1. **批量只针对单条审查内的风险**：跨审查批量仍走审核队列的批量通过/退回。
2. **没有全选按钮**：需要逐条勾选；筛选后的列表只勾选可见项。
3. **批量不填备注**：备注固定为「合同审查工作区批量处理」，逐条补备注仍走单条处置。
4. **门户概览只读**：不暴露审核状态、风险数量等内部信息，只有案件名/状态/更新时间。
5. 门户 `case` 字段对 `case_id` 为空的定向链接返回 null，前端自动隐藏。
