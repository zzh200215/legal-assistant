# 第二十八步交付说明：风险逐条处置追溯

> 一句话：律师在合同页点「接受 / 缓释 / 忽略」某条风险，除了改风险状态，还会在 `legal_review_actions` 落一条带风险下标与标题快照的动作——「谁、什么时候、忽略了哪条风险、理由是什么」可查、可展示。

- 日期：2026-10-22
- 迁移：`alembic/versions/20261022_0102_risk_action_trace.py`（MySQL 已执行，head `0101 → 0102`）
- 后端：`app/models/legal.py`（两列）、`app/services/legal/legal_domain_service.py`（写动作 + 按风险聚合历史）、`legal_workspace_service.py`（序列化补字段）
- 前端：合同页风险详情「处置记录」、审核时间线显示风险标题
- 测试：`tests/test_legal_domain_service.py`、`test_legal_domain_api.py`（断言动作行与历史）、e2e

---

## 1. 之前缺什么

step26 把风险合并进 `risks_json` 后，处置动作只做两件事：改 JSON 里的 `status`、同步 claim。谁做的、什么时候做的、理由是什么，只有一个通用的审计日志 `legal_risk_item_{action}`（没有风险定位），审核时间线里也看不到。

结果：能回答「这条风险现在是什么状态」，不能回答「它为什么变成这个状态、谁定的」。

## 2. 迁移

`legal_review_actions` 加两列（可空，不重建表）：

| 列 | 含义 |
| --- | --- |
| `risk_index` | 风险在 `risks_json` 中的下标（0 基），与风险项接口的 `id = index + 1` 对应 |
| `risk_title` | 处置时的风险标题快照（风险后续可能被改写/删除，快照保证可读） |

外加索引 `ix_legal_review_actions_risk_index`。downgrade 删列与索引；**已写下的动作行不会删除**，只是失去风险定位。

## 3. 写入

`update_risk_item_status()` 在写回 JSON 的同一个事务里插入：

```python
LegalReviewAction(
    reviewer_id=user.id, target_type="contract_review", target_id=review.id,
    action=f"risk_{action}",           # risk_accept / risk_mitigate / risk_dismiss
    note=note, from_status=current, to_status=target,
    target_version=review.version,     # 动作绑定当时的业务版本
    risk_index=risk_index, risk_title=risk.get("label"),
)
```

## 4. 读取

- `get_risk_items()`：一次查询取出该审查下所有 `risk_*` 动作（join 用户拿操作人名），按 `risk_index` 挂到对应风险项的 `history`；
- 处置接口的返回值同样带 `history`，前端处理完立即渲染，不用二次请求；
- `review-queue/{type}/{id}/history`（审核时间线）也会包含这些动作——`ReviewTimeline` 对 `risk_*` 显示「接受风险 · 付款条款」这样的标签。

返回结构（风险项内）：

```json
"history": [
  {"id": 7, "action": "risk_accept", "note": "已确认可接受", "from_status": "needs_review",
   "to_status": "accepted", "risk_index": 0, "risk_title": "付款条款",
   "reviewer_id": 3, "actor_name": "张三", "created_at": "2026-10-22T04:00:00"}
]
```

## 5. 撤销处置（reopen）

处置错了要能改：`action = "reopen"` 把风险从 accepted/mitigated/dismissed 拉回待处理（高风险回 `needs_review`，其余回 `open`），清空 `reviewer_id / resolved_at / resolution_note`，并写一条 `risk_reopen` 动作——原处置记录不删，历史里能看到「接受 → 撤销」的完整轨迹。

- 未处置的风险调用 reopen → 400（`RISK_ITEM_NOT_RESOLVED`）；
- 撤销后仍按原规则参与门禁（高风险未处理会重新阻塞发布）。

## 6. 前端

风险详情「处理状态」下方新增**处置记录**：动作（接受风险/已修改缓释/忽略风险/撤销处置）、操作人、时间、备注；已处置的风险显示「撤销处置」按钮，点击后状态与记录即时刷新。审核台时间线同步显示风险标题。

## 7. 验证

| 项 | 结果 |
| --- | --- |
| 迁移 | `alembic upgrade head` 成功，head `20261022_0102`；`risk_index`/`risk_title` 列存在 |
| 领域测试 | `test_legal_domain_service.py` + `test_legal_domain_api.py`：**32 passed**；断言动作行字段（action/risk_index/risk_title/from/to/note）与 `history`（含 actor_name），审核历史接口也能查到 `risk_*`；reopen 恢复待处理/清空处置字段/未处置时拒绝，动作序列 `risk_dismiss → risk_reopen` |
| e2e | `contract-upload`：选中付款条款 → 接受风险 → 断言请求体与处置记录；再点「撤销处置」→ 断言请求体 `{action: "reopen", ...}`、状态回到「待审核」、记录出现「撤销处置」 |
| 前端 | lint 0 error、build 通过 |

## 8. 边界

1. **复用审核动作表**，没有独立的风险处置表：`review_stats` 的 `action_distribution` 会把这些 `risk_*` 动作也计入，属于「动作总数」口径变化。
2. **撤销是追加动作、不是删除历史**：`risk_reopen` 只把状态拉回待处理，不抹掉之前的处置记录；也没有「恢复某一条历史状态」的粒度。
3. **`risk_title` 是快照**，不是外键；风险标题后来变了，历史仍显示当时的标题（这是有意的）。
4. **迁移回滚会丢定位**：downgrade 只删列，动作行仍保留但看不出是哪条风险。
5. 统计面板（step27）不区分处置动作与审核动作，没有「平均处置时长」这类指标。
