# 第三十七步交付说明：通知接入邮件渠道

> 一句话：SLA 提醒与咨询通知不再只落在站内铃铛——组织配了外发邮件策略后，同一条提醒会同时生成站内 + 邮件两个事件，邮件走既有 Outbox 投递；没配策略时行为不变（只有站内）。

- 日期：2026-10-26
- 后端：`app/services/notification/notification_service.py`（幂等键支持 `template_key`、多渠道透传、模板缺失回退）、`app/services/legal/review_sla.py`、`legal_workspace_service.py`（四类通知改走多渠道）
- 测试：`tests/test_review_sla.py`（+1）、`tests/test_notification_template.py`（+1）
- 前端：无改动（站内通知不变）

---

## 1. 幂等键升级

`_idempotency_key(..., template_key=None)`：`template_key` 参与去重，**不传时保持旧键**。

原因：同一审查会有「即将到期」和「已逾期」两个模板，若键里只有 `reference_id`，第二条会被第一条挡住。现在 `review_sla_due_soon` / `review_sla_overdue` 各自独立；历史事件的键不变。

## 2. 多渠道

`create_multi_channel_notification` 新增 `template_key` 透传；实际渠道由：

1. 组织是否配置 `OutboundEmailPolicy(enabled=True)`（`_is_email_available`）；
2. 用户通知偏好（`event_type=approval` 或 `all` 的 channels）；
3. 是否配置 SMTP 连接器（投递阶段判断）。

四类通知改为 `channels=["site","email"]`：

| 通知 | template_key |
| --- | --- |
| 无依据咨询待审核 | `review_consultation_uncited` |
| 依据偏弱咨询待审核 | `review_consultation_weak` |
| 咨询被退回（需补充依据 / 通用） | `consultation_returned_supplement` / `consultation_returned` |
| 补充完成（引用 +N） | `consultation_supplemented` |
| 审核 SLA 即将到期 / 已逾期 | `review_sla_due_soon` / `review_sla_overdue` |

站内事件仍即时 `delivered`；邮件事件 `pending`，由 `dispatch_notification_events` 领取，走邮件 Outbox（DLP、审批、SMTP 连接器）。

## 3. 邮件模板回退

这些模板键没有注册邮件模板。`_render_email_content` 原来渲染失败返回 `None` → 事件死信；现在改为**回退到事件标题/正文**（记 warning），邮件内容是纯文本提醒，不会因为缺模板而丢。

## 4. 验证

| 项 | 结果 |
| --- | --- |
| 单测 | 组织配置 `OutboundEmailPolicy(enabled=True)` 后，SLA 扫描对同一审查生成 `site/delivered` + `email/pending` 两个事件，template_key 一致；重复扫描 0 新增（幂等键含模板键） |
| 回归 | 未配置策略时事件数量不变：通知中心/投递/Outbox/模板/WS/门户通知 **41 passed**；SLA + 咨询通知测试全绿 |
| 回退 | 未注册模板键渲染时返回事件标题/正文，不再 `None` |
| 门禁 | 见提交说明 |

## 5. 边界

1. **默认不开启**：没有外发邮件策略的组织仍然只有站内通知；邮件渠道是「配置后生效」。
2. **邮件内容是纯文本标题/正文**，没有 HTML 模板；要品牌化需要注册 `NotificationTemplate`。
3. **投递依赖 worker**：`email/pending` 事件需要 `dispatch_notification_events` 与邮件 Outbox worker 运行；本地只起 web 不会发出。
4. **策略可能拦截**：`require_approval` 默认 true，邮件可能停在待审批；DLP 命中会拦截/死信。
5. **没有退订链接**：用户只能靠通知偏好关闭邮件渠道。
