# 第一百一十五步交付说明：全局请假代理

> 一句话：新增用户级「请假代理」，一次设置覆盖全部事件类型，优先级高于按事件类型的代理；沿用同一套本地日期有效期、到期提醒与自动清理。

- 日期：2026-11-11
- 后端：`models/legal_notifications.py`、`alembic/versions/20261112_0121_notification_leave.py`、`services/notification/notification_service.py`、`api/developer/notifications_onboarding.py`、`services/jobs/delegate_reminders.py`、`tasks/delegate_reminder_tasks.py`、`services/notification/email_templates.py`
- 前端：`views/NotificationPreferences.vue`、`utils/notificationLeave.js`、`api/notifications.js`
- 契约：OpenAPI **376 paths / 178 schemas / 28 error codes**（新增 3 个请假接口）
- 测试：新增后端 **43 passed**（服务 18 / 接口 13 / 任务 12）、前端单测 **7 条**
- 迁移：**122 个**，head `20261112_0121`

---

## 1. 为什么单独一张表

按事件类型配置代理要配 7 次，真实请假（出差、年假、病假）还要逐类勾一遍，容易漏。请假表达的是「整段时间不在」，是用户级而不是事件级设置，因此：

- 存 `legal_notification_leaves`，每用户每组织唯一一条；
- 不塞进 `legal_notification_preferences.event_type` 枚举，避免污染偏好列表、渠道解析与历史数据；
- 解析收件人时排在事件类型代理之前：请假 > 事件类型代理 > 本人。

## 2. 后端

### 2.1 模型与迁移

`legal_notification_leaves`：

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `user_id` | int, NOT NULL | 请假的用户（原收件人） |
| `organization_id` | int, NOT NULL | 组织隔离；跨组织配置不生效 |
| `delegate_user_id` | int, NULL | 请假期间的代理接收人 |
| `leave_start` | DATE, NULL | 开始日期（本地日期，含当天；NULL=不限） |
| `leave_end` | DATE, NULL | 结束日期（本地日期，含当天；NULL=不限） |
| `note` | varchar(256), NULL | 备注（如「出差」），仅展示 |
| `updated_at` | datetime, NULL | 更新时间 |

- 唯一约束 `uq_legal_notification_leaves_user_org (user_id, organization_id)`；
- `user_id` / `organization_id` 建索引，`delegate_user_id` 由外键自动建索引；
- 迁移 **0121**：MySQL 实库已升级到 `20261112_0121`（列类型 / 可空 / 唯一索引已核验）；SQLite 上单独验证了 0121 的 `upgrade` 与 `downgrade`（建表、2 个索引 + 唯一索引、回滚删表）。

### 2.2 解析优先级

`create_multi_channel_notification(..., allow_delegate=True)` 的收件人解析顺序：

```
allow_delegate=False            → 本人（升级、投递告警等强提醒）
全局请假生效                     → (请假代理, 原收件人)
事件类型代理生效                  → (代理, 原收件人)
其余                            → 本人
```

- **只跳一层**：不会跟随代理自己的代理/请假；
- **无效配置逐层回退**：请假代理被停用、跨组织、指向自己、不在有效期 → 先回退到事件类型代理，再回退本人，不会因为请假设置坏掉而丢通知；
- **时区口径**：请假窗口按用户「默认（全部事件）」偏好的时区判断「今天」，没有该偏好按 `Asia/Shanghai`。请假是全局设置，不能跟着某一种事件类型的时区走，否则同一段请假在不同事由下会有两个「今天」；
- **渠道与静默跟随代理**：与事件类型代理一致，用代理自己的渠道偏好与静默窗口；
- 命中代理时正文前缀「代 X 接收」，事件 `user_id=代理`、`on_behalf_of_user_id=原收件人`，幂等键纳入原收件人。

### 2.3 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/developer/notification-leave/me` | 读取请假设置与状态 |
| PUT | `/api/developer/notification-leave/me` | 设置/更新；代理传 `null` 等价结束请假 |
| DELETE | `/api/developer/notification-leave/me` | 结束请假（幂等） |

返回字段：`delegate_user_id`、`delegate_name`、`leave_start`、`leave_end`、`note`、`timezone`、`status`、`days_left`、`updated_at`。

- `status`：`none`（未设置）/ `scheduled`（已排期，未到开始日）/ `active`（请假中）/ `expired`（已过期，等待到期任务清理）；
- `days_left`：距结束日天数（含当天为 0，过期可为负）；
- 校验：无组织 422、代理必须是同组织成员 422、不能转给自己 422、开始晚于结束 422、备注 >200 字 422；
- 没有代理时后端把开始/结束/备注一起清空，页面不会出现「没有代理却有一段有效期」；
- 写操作记审计 `notification_leave_set`（含代理、起止）。

### 2.4 到期提醒与清理

复用每天 **09:10 Asia/Shanghai** 的 `check_delegate_expiring` 任务（任务名保持不变，避免改 beat 配置与外部监控口径）：

- 提前 **2 天**提醒原收件人一次（站内 + 邮件），标题 `请假代理将于 YYYY-MM-DD 到期`，模板 `leave_expiring_soon`，`reference_type=notification_leave`；
- 提醒 `allow_delegate=False`：这条消息本身就是「代理要结束了」，不能再转给代理；
- 去重：用户 + 模板 + 标题；延长结束日期得到新标题，重新提醒一次；
- 到期清理：清空代理、起止日期与备注，通知自动恢复发给本人；
- 扫描返回值扩展为 `leave_created` / `leave_scanned` / `leave_cleared`，与事件类型代理的 `created` / `scanned` / `cleared` 并列；
- 邮件模板新增 `leave_expiring_soon`，深链 `notification_leave → /settings/notifications`。

## 3. 前端

`/settings/notifications` 顶部新增「请假代理（全部事件）」卡片：

- 状态胶囊：未设置 / 已排期 / 请假中 / 已过期；
- 摘要：`代 协办律师 接收 · 2026-11-10 至 2026-11-20 · 剩余 9 天`（无结束日期显示「长期有效」，当天结束显示「今天结束」）；
- 表单：代理下拉、备注（≤200 字）、请假开始/结束；
- 「保存请假」下推 PUT；「结束请假」下推 DELETE；选「不设置」再保存等价于结束请假，不会卡在校验里；
- 已过期时给出提示「请假已结束，通知已恢复发送给你；到期任务会自动清理」；
- 校验：必须选代理、开始不能晚于结束、备注不超长；
- 表单转换与摘要集中在 `utils/notificationLeave.js`，7 条单测覆盖状态标签、回显、payload 空值折叠与校验。

## 4. 验证

新增 `tests/test_notification_leave.py`（18 条，服务层）：

- 请假覆盖事件类型代理；覆盖没有单独配置的事件类型；
- 未到开始日回退到事件类型代理；已过期、停用、跨组织、指向自己回退；
- 起止含当天、单日窗口、仅开始/仅结束；
- 默认偏好时区决定「今天」；`user_timezone` 缺失回退上海；
- 两个原收件人同一代理不互相去重；结束请假恢复本人；
- 任务提醒走请假代理；逾期升级不代理；状态标签四态。

新增 `tests/test_notification_leave_api.py`（13 条）：

- 默认 `none`；设置后 `active` + 代理姓名 + `days_left` + 备注去空格；
- `scheduled` / `expired`；清空代理同时清日期与备注；重复 PUT 只留一行；
- 跨组织 / 转给自己 / 日期倒挂 / 不存在代理 / 备注超长 / 无组织 422；
- DELETE 清空且幂等。

新增 `tests/test_leave_expiry_reminder.py`（12 条）：

- 提前 2 天、当天、明天；超过 2 天不提醒；
- 到期清空代理/起止/备注；清理幂等；无结束日期跳过；
- 与事件类型代理同一次扫描各提醒一次（共 4 条 site+email）；
- 按默认偏好时区判断；重复扫描不重复；延长结束日期重新提醒；
- 提醒只发本人；静默窗口把请假提醒邮件推迟。

原有 `tests/test_delegate_expiry_reminder.py` 的两处返回值断言同步补上 `leave_*` 计数（行为不变）。

| 项 | 结果 |
| --- | --- |
| 新增后端测试 | 43 passed |
| 通知/任务相关回归 | 163 passed（13 个文件） |
| 前端单测 | 120 passed（新增 7） |
| 任务/通知 e2e | 21 passed（通知偏好用例扩展请假区块） |
| OpenAPI / 迁移 | **376 paths / 178 schemas** / 122 |
| 包体 | 入口 **84.2 / 100**（gzip **29.1 / 35**），总资产 **1689.1 / 1700**（99%，进预警档） |

## 5. 门禁

- `scripts/check_all.py`：**11/11，exit 0，15.1 分钟**。
- 后端全量：**2653 passed / 4 skipped**（13 分 40 秒）；OpenAPI **376 paths / 178 schemas / 28 error codes**；前端单测 **120 passed**；迁移 **122**，head `20261112_0121`。
- 包体：入口 **84.2 / 100 KB**（gzip 29.1 / 35）、最大页面 chunk **85.3 / 130 KB**、总资产 **1689.1 / 1700 KB**；总资产已用 **99%**，触发 >90% 预警（预算仍通过）。

## 6. 边界

1. **只影响新通知**：请假设置之前已创建的通知不会改发，也不会补发。
2. **只跳一层**：请假代理自己再设代理/请假，不会链式转发；那是代理自己的设置，只影响发给代理的通知。
3. **时区口径单一**：请假窗口固定按「默认（全部事件）」偏好的时区；单条事件类型的时区不参与请假判断。
4. **不改渠道与静默**：请假只改收件人，用代理自己的渠道与静默；原收件人的静默不影响转发。
5. **通知中心不区分来源**：标签统一「代 X 接收」，看不出是请假代收还是事件类型代理代收。
6. **清理保留行**：到期只清空字段、不删行，没有历史版本；审计只有 `notification_leave_set`，到期清理不单独写审计。
7. **任务名未改**：`check_delegate_expiring` 现在同时扫描请假，任务名/beat id 保持不变，外部监控看到的名字仍是旧名。
8. **允许重叠配置**：请假期间仍可给单个事件类型设代理，但会被请假覆盖（页面对此有提示）。
9. **包体接近预算**：入口 +0.2 KB（84.0 → 84.2）、总资产 +6.0 KB（1683.1 → 1689.1），已用 **99%**，只剩 10.9 KB；门禁通过但触发预警，下一个功能需要先腾空间。

## 7. 后续建议

- 通知中心区分「请假代收 / 事件类型代收」，并给请假中的人一个全局提示；
- 请假开始时给代理发一条「你开始代 X 接收通知」的确认通知；
- 请假到期前给代理也提醒一次（现在只提醒本人）；
- 请假与「减少打扰」联动：请假期间本人不再收到低优先级摘要。
