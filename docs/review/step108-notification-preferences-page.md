# 第一百零八步交付说明：通知偏好设置页

> 一句话：新增 `/settings/notifications`，按事件类型配置邮件渠道、静默时段与时区；补后端入参校验与显式 null 清除，关掉 step43 留下的「偏好没有前端入口」缺口。

- 日期：2026-11-07
- 后端：`api/developer/notifications_onboarding.py`
- 前端：`views/NotificationPreferences.vue`、`utils/notificationPreferences.js`、`api/notifications.js`、`router.js`、`App.vue`、`views/Notifications.vue`
- 契约：OpenAPI 仍 **375 paths / 177 schemas / 28 error codes**，快照因偏好请求字段校验更新（无 breaking）
- 测试：新增后端 **12 passed**、前端单测 **6 passed**、e2e **1 passed**；门禁 e2e 合计 **21 passed**
- 无迁移（仍 119 个，head `20261102_0118`）；包体 **1680.4 / 1700 KB**（余 19.6 KB）

---

## 1. 入口与页面

- 路由：`/settings/notifications`，登录用户可访问，不需要管理员权限；
- 入口：通知中心页头新增「通知偏好」按钮；
- 页面左侧是 7 个事件类型，右侧是当前类型的表单，保存只作用于当前类型：

| 事件类型 | 含义 |
| --- | --- |
| `all` | 默认（全部事件），未单独配置的类型回退到这里 |
| `deadline` | 关键日期（任务到期/逾期） |
| `approval` | 审核 |
| `invoice` | 账单 |
| `sign` | 签署 |
| `portal` | 客户门户 |
| `workflow` | 案件工作流 |

- 左侧会显示「已配置 / 默认 / 未配置」，切换类型丢弃未保存修改（页面有提示）。

## 2. 表单字段

| 字段 | 行为 |
| --- | --- |
| 站内通知 | 固定勾选、禁用；后端始终创建站内事件 |
| 邮件 | 可开关；组织未配置外发策略时后端不会真正投递 |
| 静默开始/结束 | 原生 `time` 输入，支持跨夜 22:00–07:00；只填一端会被前端拦住 |
| 时区 | 8 个常用时区 + 当前值；用于任务提醒判断「今天/明天」和静默窗口解释 |

- 已有偏好中的微信/飞书渠道不在页面展示，但保存时会原样保留，不会因为改邮件开关而丢失；
- 代理接收人、摘要频率：后端 API 已支持并有测试，但当前投递链路尚未消费，页面先不暴露，避免出现“能点但不生效”的控件；保存时会保留原值。

## 3. 后端校验与清除语义

`PUT /api/developer/notification-preferences/me?event_type=...`

- 事件类型必须在 `EVENT_TYPES` 内，否则 422；
- 用户没有组织时不能保存（422），避免写入 `organization_id=0`；
- `channels_json` 必须是字符串数组，渠道只能是 `site/email/wechat/feishu`，重复项去重后存回；
- `mute_start/mute_end` 只接受 `HH:MM`，空串或 `null` 表示清除；
- `timezone` 必须能被 `ZoneInfo` 解析；
- `delegate_user_id` 必须是同组织用户，`null` 表示清除；
- `summary_frequency` 只接受 `none/daily/weekly`，`null` 归一为 `none`；
- 只更新请求里出现的字段（`model_dump(exclude_unset=True)`），显式 `null` 才会清除；
- GET 按当前组织过滤，避免切换组织后读到旧组织偏好。

## 4. 验证

后端（`tests/test_notification_preference_api.py`，12 条）：

- 空列表、创建后回读、部分更新保留其它字段；
- 显式 null 清除静默与代理；
- 非法事件类型/渠道/时区/静默/摘要频率均 422；
- 代理接收人跨组织 422；无组织用户 422；
- GET 只返回当前组织。

前端单测（`src/utils/notificationPreferences.test.js`，6 条）：channels_json 解析、默认表单、已有偏好回显、保存 payload、去重、静默成对/相同/时区校验。

E2E（`tests/e2e/notification-preferences.spec.js`，1 条）：切换事件类型 → 回显 → 改邮件与静默 → 保存下推 → 默认类型只填一端被拦 → 补齐后保存。

| 项 | 结果 |
| --- | --- |
| 偏好接口单测 | 12 passed |
| 前端偏好单测 | 6 passed |
| 偏好 e2e | 1 passed（全量 mock 合计 21 passed） |
| 前端单测总数 | 112 passed |
| OpenAPI / 迁移 | **375** / 119（无新迁移） |
| 包体 | 入口 83.9 / 100（gzip 29.0 / 35），总资产 **1680.4 / 1700** |

## 5. 门禁

- `scripts/check_all.py`：**11/11，exit 0**；e2e 段现在同时跑任务中心与通知偏好两个 spec。
- 后端全量：**2564 passed / 4 skipped**；OpenAPI **375**；前端单测 **112 passed**。
- 包体：入口 **83.9 / 100 KB**、最大页面 chunk **85.3 / 130 KB**、总资产 **1680.4 / 1700 KB**。

## 6. 边界

1. **静默当时只对系统投递告警生效**：任务/审核通知邮件当时不读静默时段；该项已由 [step109](step109-quiet-hours-delivery.md) 接入（延迟到窗口结束）。
2. **代理接收人/摘要频率未接入投递**：API 可存可读，页面不暴露；避免误导。
3. **站内通知不可关闭**：这是既有产品口径，页面只把站内显示为固定开启。
4. **微信/飞书渠道未暴露**：已有配置保留，但没有连接器时暴露开关会误导。
5. **摘要频率没有定时任务**：字段只是偏好存储，暂无汇总发送任务消费。
6. **页面只按事件类型配置**：没有组织级默认、没有按案件覆盖（案件级策略仍走 `notification-policy` 接口）。
7. **包体只剩 19.6 KB**：新增页面 chunk 6.36 KB + CSS 4.01 KB，下一次加页面级功能前要先量产物。

## 7. 后续建议

- 把静默时段接入普通邮件：命中窗口时延迟到窗口结束再投递，或降级为仅站内（step109 已实现延迟方案，见 [step109](step109-quiet-hours-delivery.md)）；
- 代理接收人真正生效：投递前解析代理并在事件里记录原收件人；
- 摘要任务：按 `summary_frequency` 聚合每日/每周未读；
- 组织级默认偏好，减少每个成员重复配置。
