# 第一百一十四步交付说明：通知中心「代 X 接收」标签与筛选

> 一句话：通知中心与铃铛给代收事件加「代 X 接收」标签，并支持按来源筛选（全部 / 本人 / 代收）；服务端按 `on_behalf_of_user_id` 过滤，批量补代收人姓名。

- 日期：2026-11-11
- 后端：`services/notification/notification_service.py`、`api/developer/notifications_onboarding.py`
- 前端：`views/Notifications.vue`、`components/legal/NotificationBell.vue`
- 契约：OpenAPI 仍 **375 paths / 177 schemas / 28 error codes**（`GET /notifications/me` 新增可选 `delegated` 参数）
- 测试：新增代收筛选接口单测 **4 passed**；任务 e2e 扩展 1 条
- 无迁移（仍 121 个，head `20261111_0120`）；包体 **1683.1 / 1700 KB**（余 16.9 KB）

---

## 1. 后端

`GET /api/developer/notifications/me` 新增：

| 参数 | 值 | 含义 |
| --- | --- | --- |
| `delegated` | `true` | 只看代收（`on_behalf_of_user_id IS NOT NULL`） |
| `delegated` | `false` | 只看本人（`on_behalf_of_user_id IS NULL`） |
| `delegated` | 不传 | 全部 |

- 过滤同时作用于列表与 `total`，分页数字一致；
- 可与 `status`、`event_type`、`case_id`、`page/page_size` 组合；
- 列表项新增 `on_behalf_of_name`：批量查询原收件人，优先 `full_name`，回退 `username`，再回退「用户 #id」；
- 本人通知的 `on_behalf_of_name` 为 `null`，前端无需判空字段存在性。

## 2. 前端

- 通知中心工具栏新增「来源」下拉：全部来源 / 本人 / 代收，切换后重置到第 1 页；
- 列表标题行在事件类型旁显示胶囊标签「代 主办律师 接收」；
- 铃铛面板同样显示「代 X 接收」标签；
- 标签只在 `on_behalf_of_user_id` 非空时出现，本人通知不显示；
- 过滤参数下推服务端，不做前端过滤。

## 3. 验证

新增 `tests/test_notification_delegate_filter.py`（4 条）：

- 不带参数返回本人 + 代收；
- `delegated=true` 只返回代收，且姓名正确（有 `full_name` 用姓名，没有则 username）；
- `delegated=false` 只返回本人，`on_behalf_of_name=null`；
- 与事件类型、分页组合时 `total` 仍按筛选口径。

E2E（`tasks-flow.spec.js` 通知中心用例扩展）：

- 两条通知（本人 + 代收）同时展示，代收项显示「代 主办律师 接收」；
- 来源切「代收」→ 请求带 `delegated=true`，只显示代收；
- 切「本人」→ `delegated=false`，只显示本人；
- 切「全部」→ 不带参数，两条都显示；
- 点击本人任务提醒仍直达 `/tasks?taskId=101`。

| 项 | 结果 |
| --- | --- |
| 代收筛选接口单测 | 4 passed |
| 通知相关回归 | 58 passed |
| 前端单测 / 任务与通知 e2e | 113 / 21 passed |
| OpenAPI / 迁移 | **375** / 121（无新迁移） |
| 包体 | 入口 84.0 / 100（gzip 29.1 / 35），总资产 **1683.1 / 1700** |

## 4. 门禁

- `scripts/check_all.py`：**11/11，exit 0**。
- 后端全量：**2610 passed / 4 skipped**；OpenAPI **375**；前端单测 **113 passed**。
- 包体：入口 **84.0 / 100 KB**、最大页面 chunk **85.3 / 130 KB**、总资产 **1683.1 / 1700 KB**。

## 5. 边界

1. **姓名是读取时快照**：代收人改名后，历史通知显示新名字，不保留当时姓名。
2. **只覆盖站内通知**：`/notifications/me` 只查 site 渠道；邮件代收不在此列表。
3. **铃铛没有筛选**：铃铛只显示标签，筛选只在通知中心页。
4. **筛选不写 URL**：刷新或重新进入页面恢复「全部来源」。
5. **没有代收统计**：未读/总数不区分本人与代收。
6. **旧事件没有标签**：step110 之前的事件没有 `on_behalf_of_user_id`，按本人处理。
7. **包体略增 0.1 KB 入口 / 1.0 KB 总量**：铃铛在入口 chunk，标签与筛选样式在通知页 chunk。

## 6. 后续建议

- 筛选写回 URL，支持分享/返回恢复；
- 通知中心增加「代收」统计与未读分桶；
- 全局请假模式：一次设置所有事件类型的代理与有效期；
- 摘要任务按 `summary_frequency` 聚合。
