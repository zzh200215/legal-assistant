# 第八十八步交付说明：任务到期提醒（提前 1 天 / 当天）

> 一句话：Celery beat 每 15 分钟扫描任务，在截止日期前一天与当天各发一次站内通知（组织配了外发策略时同时走邮件 Outbox），通知可直接点进任务详情；同任务同阶段只发一次。

- 日期：2026-10-31
- 后端：`services/jobs/task_reminders.py`、`tasks/task_reminder_tasks.py`、`core/celery_app.py`、`services/notification/email_templates.py`
- 前端：`components/legal/NotificationBell.vue`
- 测试：新增提醒单测 **6 passed**；任务 e2e **8 passed**（新增通知直达）；前端单测 **100 passed**
- 无迁移；OpenAPI 无变化（370）；包体 **1648.8 / 1700 KB**（余 51.2 KB）

---

## 1. 为什么补

step86 让任务可以设置截止日期，但截止日期只参与展示：

- 用户不主动看任务中心就不知道任务要到期；
- 逾期统计只能事后发现；
- 通知中心已有 Outbox/偏好/邮件能力，但没有任务类事件。

## 2. 规则

| 阶段 | 触发条件 | 模板键 | 标题 |
| --- | --- | --- | --- |
| 提前 1 天 | `due_date` 的日期 = 明天 | `task_due_tomorrow` | 任务明天到期：{标题} |
| 当天 | `due_date` 的日期 = 今天 | `task_due_today` | 任务今天到期：{标题} |

- 只处理 `status ∈ (todo, in_progress)` 且有 `due_date` 的任务；
- 已完成/已取消/无截止日期/已逾期（日期早于今天）不提醒；
- 每个任务每个阶段只发一次，用 `LegalNotificationEvent(reference_type="task", reference_id, template_key)` 去重；
- 「提前 1 天」和「当天」是两个独立阶段：明天提醒过，改成今天后仍会再提醒一次；
- 通知发给任务 owner；任务必须有组织（task.organization_id，缺失时回退 owner 的 organization_id），否则跳过。

## 3. 投递链路

- 站内：`create_multi_channel_notification(channels=["site","email"])`，站内事件同步置 `delivered`；
- 邮件：仅当组织配置了外发策略/邮件通道时才登记 email 事件，实际由 Outbox worker 投递（复用既有重试/死信）；
- 主题/正文：新增两个内置 HTML 邮件模板（`task_due_tomorrow` / `task_due_today`），未配置数据库模板时也能发出完整邮件；
- 去重键包含 `template_key`，任务的两个阶段互不覆盖。

## 4. 通知直达任务

`NotificationBell` 新增 `reference_type === "task"` 分支：

- 跳 `/tasks?taskId={id}`，任务中心自动打开详情；
- 有 `case_id` 时追加 `case_id`，保留案件上下文。

## 5. 调度

- Celery beat：`check-task-due-reminders`，每 900 秒（15 分钟）；
- Celery task：`check_task_due_reminders`，带 `beat_lock` 防重入、记录 beat 心跳；
- 扫描按服务器本地日期判断「今天/明天」（与 step86 的 naive 本地时间口径一致）。

## 6. 验证

后端新增 `tests/test_task_due_reminders.py`：

- 明天到期 + 今天到期各发一条；已逾期/更晚/已完成/已取消/无日期不发；
- 重复扫描幂等（第二次 created=0，事件数不变）；
- 明天提醒后改到今天 → 再发当天提醒，两个模板键并存；
- 组织配置外发策略 → 同一任务同时有 `site(delivered)` + `email(pending)` 两条事件；
- 无组织任务跳过并计数；
- 任务有 `case_id` 时通知保留案件，组织回退到 owner；
- beat 注册了 `check_task_due_reminders` 且周期 900s。

任务 e2e：

- 通知中心出现「任务今天到期」→ 点击后 URL 带 `taskId=601`，任务详情自动打开，标题与截止日期正确。

同步更新两处既有测试：

- 内置模板注册表 / 模板列表测试：业务模板 9 → 11；
- Outbox 静默时段用例：静默窗口改为「当前前后 1 小时」，避免在 23:59 分钟边界抖动（原 `00:00-23:59` 在 23:59 这一分钟不命中）。

| 项 | 结果 |
| --- | --- |
| 新增提醒单测 | 6 passed |
| 任务 e2e | 8 passed |
| 前端单测 | 100 passed |
| OpenAPI / 迁移 | 370（无变化）/ 无（head 仍 `20261030_0114`，115 个） |
| 包体 | 入口 82.9 / 100（gzip 28.7 / 35），总资产 **1648.8 / 1700** |

## 7. 门禁

- `scripts/check_all.py`：**10/10，12.9 分钟，exit 0**（本树最终状态）。
- 后端全量：**2490 passed / 4 skipped**；OpenAPI **370**；前端单测 **100 passed**。
- 包体：入口 **82.9 / 100 KB**、最大页面 chunk **85.3 / 130 KB**、总资产 **1648.8 / 1700 KB**。

## 8. 边界

1. **按日期而非精确时刻**：只要截止日期是明天/今天，下次扫描就发；不区分 09:00 还是 23:59。
2. **服务器本地时区**：due_date 是 naive 本地时间，提醒按服务器本地日期算；没有按用户偏好时区（`LegalNotificationPreference.timezone`）换算。
3. **只提醒 owner**：负责人/协作者是自由文本，不是用户 ID，无法可靠投递。
4. **只提醒两个阶段**：逾期不再补发「已逾期」提醒；也没有提前 3 天/7 天配置。
5. **同阶段只发一次**：改期到别的日期再改回来，不会重置已发标记；要重发需人工删事件。
6. **最多延迟 15 分钟**：beat 每 15 分钟扫一次；beat/worker 停摆时不会补发（站内也不发）。
7. **邮件依赖组织配置**：没有外发策略/SMTP 时只落站内事件，不报错。
8. **全表扫描**：查询 `tasks` 中 `status ∈ (todo,in_progress) AND due_date IS NOT NULL`，当前表无 `(status,due_date)` 索引；任务量大时需要加索引或改增量扫描。
9. **不做静默/免打扰之外的节流**：通知偏好/静默时段由既有通知服务处理，本功能不额外加频控。
10. **无迁移/无 OpenAPI 变化**：去重复用通知事件表；前端只加一个跳转分支。

## 9. 后续建议

- 逾期提醒（逾期 1 天/3 天）；
- 按用户时区计算「今天/明天」；
- 可配置提前天数（3/7 天），复用通知策略表；
- 任务量大时给 `tasks(status, due_date)` 加索引；
- 负责人改为真实用户后，可抄送负责人/协作者。
