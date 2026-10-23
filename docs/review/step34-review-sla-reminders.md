# 第三十四步交付说明：审核 SLA 站内提醒（定时扫描）

> 一句话：不用盯着审核台页头——每 15 分钟扫一次到期待审记录，给审核人发「审核即将到期 / 审核已逾期」的站内通知；同一阶段只发一次，从「即将到期」变成「已逾期」会再提醒。

- 日期：2026-10-23
- 后端：`app/services/legal/review_sla.py`（`scan_review_sla`）、`app/tasks/legal_tasks.py`（`check_legal_review_sla`）、`app/core/celery_app.py`（beat 15 分钟 + notification 队列路由）
- 前端：无改动（通知铃铛/通知中心已有；点击按 `reference_type` 跳转）
- 测试：`tests/test_review_sla.py`（2 例）、`tests/test_task_routing.py`（任务路由自动覆盖）

---

## 1. 扫描范围

对 `legal_consultations` / `legal_contract_reviews` / `legal_drafts` 三类记录：

| 条件 | 说明 |
| --- | --- |
| `status in (pending_review, needs_lawyer_review, needs_facts)` | 只提醒仍在审核中的 |
| `review_due_at` 非空 | 没设截止时间不提醒 |
| `reviewer_id` 非空 | 只提醒已分配的审核人；未分配不催 |

阶段判定：`due - now < 0` → 已逾期；`0 ≤ 剩余 ≤ 24h` → 即将到期；其余跳过。

## 2. 通知内容

| 阶段 | 标题 | template_key |
| --- | --- | --- |
| 即将到期 | `审核即将到期：六小时后到期（剩 6 小时）` | `review_sla_due_soon` |
| 已逾期 | `审核已逾期：已超时两天（超时 2 天）` | `review_sla_overdue` |

正文带去重标记与截止时间：`sla:due_soon · 咨询 #12 的审核截止时间 2026-10-23 18:00`。
站内通知直接 `delivered`，落库即可见（不依赖 Outbox 投递）。

## 3. 去重与阶段升级

- 同一审查 + 同一阶段（`reference_type + reference_id + body 含 sla:<stage>`）只写一条；
- 同一审查从「即将到期」进入「已逾期」会再发一条（阶段变了）；
- 重新分配审核人不会重置已发提醒（同阶段仍不重复）。

## 4. 运行方式

- Celery beat：`check-legal-review-sla`，每 900 秒；
- 任务走 `notification` 队列（120s 超时），带 `beat_lock` 防多实例重复；
- 也可手动调用 `scan_review_sla(db)` 做补发/对账。

## 5. 验证

| 项 | 结果 |
| --- | --- |
| 单测 | 6h 内到期 + 超时 50h → 各 1 条（标题/模板键/组织正确）；72h 后到期不提醒；未分配不提醒；重复扫描 0 新增 |
| 单测 | 先「即将到期」后（模拟时间推进）「已逾期」→ 同一审查两条不同阶段的提醒 |
| 任务路由 | `check_legal_review_sla` 已注册、路由到 notification 队列（`test_task_routing` 通过） |
| 门禁 | 见提交说明 |

## 6. 边界

1. **只提醒已分配审核人**：未分配的记录不会催（分配动作本身仍可另行通知）。
2. **没有升级链**：逾期后只发一次，不会每小时催、也不会通知管理员。
3. **依赖 Celery beat**：本地只跑 web 不起 worker/beat 时不会自动提醒；手动调用 `scan_review_sla` 可补。
4. **时间是 UTC naive**：与库内 `review_due_at` 同口径；前端展示按本地时间。
5. **改期不重置提醒**：同一审查同一阶段只发一次，改期后不会重新提醒。
