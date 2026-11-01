# 第八十九步交付说明：逾期提醒 + 按用户时区计算今天/明天

> 一句话：任务提醒从「提前 1 天 / 当天」扩到「逾期 1 天 / 逾期 3 天」，四个阶段各发一次；「今天/明天/逾期」改按任务 owner 的通知偏好时区计算，服务器时区不再影响提醒日期。

- 日期：2026-11-01
- 后端：`services/jobs/task_reminders.py`、`services/notification/email_templates.py`
- 前端：无改动（通知中心沿用 step88 的 `reference_type=task` 直达）
- 测试：提醒单测 **8 passed**；模板/API 相关 **26 passed**；前端单测 **100 passed**
- 无迁移；OpenAPI 无变化（370）；包体维持 **1648.8 / 1700 KB**（余 51.2 KB）

---

## 1. 四个阶段

| 阶段 | 条件（`delta = due_day - user_today`） | 模板键 | 标题 |
| --- | --- | --- | --- |
| 提前 1 天 | `delta = 1` | `task_due_tomorrow` | 任务明天到期 |
| 当天 | `delta = 0` | `task_due_today` | 任务今天到期 |
| 逾期 1 天 | `delta = -1` | `task_overdue_1d` | 任务已逾期 1 天 |
| 逾期 3 天 | `delta = -3` | `task_overdue_3d` | 任务已逾期 3 天 |

- 只处理 `status ∈ (todo, in_progress)` 且有 `due_date` 的任务；
- 逾期 2 天/4 天及更早不提醒（避免每天刷屏）；
- 每个任务每个阶段只发一次，依赖 `LegalNotificationEvent(reference_type="task", reference_id, template_key)` 去重；
- 逾期通知的正文带「（已逾期 N 天）」，站内/邮件模板各新增两条（`task_overdue_1d` / `task_overdue_3d`）。

## 2. 用户时区

此前按服务器本地日期判断，跨时区团队会错一天。现在：

1. `now` 统一按 UTC 解释（Celery 传 `utc_now()`；单测可传 aware/naive，naive 视为 UTC）；
2. 每个任务按 `(user_id, organization_id)` 取生效通知偏好：`event_type=deadline` → 回退 `all` → 默认 `Asia/Shanghai`；
3. 用偏好里的 `timezone` 把 `now` 换成本地日期，再与 `due_date` 的日期比较；
4. 同一用户/组织的时间换算按扫描批次缓存，避免 N+1。

`due_date` 仍是 naive 墙上时间（step86 起前端写当天 23:59:59），因此「日期」直接按用户本地日历解释；用户改时区后，历史日期的语义会随之改变，这是 naive 存储的已知限制。

## 3. 返回与日志

`scan_task_due_reminders` 返回：

```
{created, due_tomorrow, due_today, overdue_1d, overdue_3d, skipped_no_org}
```

- `skipped_no_org` 只统计「命中阶段但任务/owner 都没有组织」的条数；
- beat 任务仍为 `check_task_due_reminders`，每 15 分钟，带 `beat_lock` 与心跳。

## 4. 验证

`tests/test_task_due_reminders.py` 新增/更新：

- 四个阶段各发一次；逾期 2 天/4 天、还早、已完成、已取消、无日期都不发；
- 重复扫描幂等（第二次全 0，事件数不变）；
- 明天提醒后改今天 → 再发当天提醒；
- 逾期 1 天/3 天模板与正文「已逾期 N 天」；
- **时区用例**：偏好 `America/New_York`，`2026-12-31T02:00Z`（纽约 12-30 21:00、上海 12-31 10:00）：
  - 截止 12-30 → 纽约「今天」；
  - 截止 12-31 → 纽约「明天」；
  - 截止 12-29 → 纽约「逾期 1 天」；
  - 若按服务器/上海日期，这三条会分别变成逾期 1 天、今天、逾期 2 天，用例能明确区分。
- 外发策略下站内+邮件、无组织跳过、`case_id` 保留、beat 注册仍覆盖。

模板注册表测试同步 13 个业务键（原 11）。

| 项 | 结果 |
| --- | --- |
| 提醒单测 | 8 passed |
| 模板/API 相关 | 26 passed |
| 前端单测 | 100 passed |
| OpenAPI / 迁移 | 370（无变化）/ 无（head 仍 `20261030_0114`，115 个） |
| 包体 | 入口 82.9 / 100（gzip 28.7 / 35），总资产 **1648.8 / 1700** |

## 5. 门禁

- `scripts/check_all.py`：**10/10，13.7 分钟，exit 0**（本树最终状态）。
- 后端全量：**2492 passed / 4 skipped**；OpenAPI **370**；前端单测 **100 passed**。
- 包体：入口 **82.9 / 100 KB**、最大页面 chunk **85.3 / 130 KB**、总资产 **1648.8 / 1700 KB**。

## 6. 边界

1. **只发四个固定阶段**：逾期 2/4/5 天不再提醒，也没有「每天提醒直到完成」；要改需扩 `STAGES`。
2. **精确日期匹配**：beat 在当天没跑或任务在当天之后才创建，会错过该阶段；不会补发。
3. **不给自由文本负责人发**：负责人/协作者是姓名，不是用户 ID，仍只提醒 owner。
4. **时区取通知偏好**：没有偏好时默认 `Asia/Shanghai`；用户时区改了，历史 naive 截止日期的语义随之变化。
5. **`due_date` 仍是 naive 墙上时间**：不是 UTC 时间戳；跨时区协作的「同一时刻」无法精确表达。
6. **最多延迟 15 分钟**：beat 周期决定；beat/worker 停摆不会补发。
7. **邮件依赖组织配置**：无外发策略时只落站内事件。
8. **全表扫描**：仍按 `status ∈ (todo,in_progress) AND due_date IS NOT NULL` 扫描；无 `(status,due_date)` 索引。
9. **同阶段只发一次**：改期后回到同一阶段不会重发；需要人工删事件。
10. **无前端改动/无迁移**：通知直达仍走 step88 的 `taskId` 深链。

## 7. 后续建议

- 可配置提前/逾期天数（复用通知策略表 `advance_days_json`）；
- 给 `tasks(status, due_date)` 加索引，或改成按日期窗口增量扫描；
- 逾期后支持「每日一次直到完成」的可选策略；
- 负责人改为真实用户后，抄送负责人/协作者；
- 截止日期改为带时区时间戳，彻底解决 naive 语义。
