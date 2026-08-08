# PRD：客户门户 P1 打磨（下一迭代产品需求计划）

## 背景（Context）

本产品（AI 法律助手 / 律智检）为个人模拟项目。上一轮迭代已完成客户门户基础能力（OTP 鉴权、进度时间线、文书/PDF 下载、默认 30 天过期、品牌化、客户反馈），但 `docs/portal-polish-checklist.md`（#79）P1 三项中仍有缺口：

- **「链接到期策略：默认 30 天过期 + 自动撤销提醒（邮件/飞书通知到律师）」** → 过期检测已有（每小时扫描置 `expired`，见 `app/tasks/__init__.py:745`），但**无任何通知触达律师**。
- **「移动端最小适配」** → 仅 640px 基础断点，未验证且有溢出点。

同时，通知系统后端（`notification_service` + `LegalNotificationEvent`）已建好，但**前端无任何查看入口**，通知事件对律师不可见。本次迭代以 **MVP 够用即可** 补齐 P1：到期/即将到期自动通知律师 + 最小站内通知中心（作为通知的可见载体）+ 管理端链接状态可见性 + 移动端最小适配。**不需要模拟数据管线**（用户已确认）。

范围：仅 P1；不含 P2/P3（访问分析入周报、品牌化扩展、聚合页、客户可见账单）。

## 一、目标（Goals）

- 律师在门户链接**即将到期（3 天内）**与**已过期**时收到站内通知，可在失效前续期或联系客户
- 通知在系统中**可见可读**（顶栏铃铛 + 未读角标），避免「通知写了但没人看得到」
- 管理端链接列表可直接看到剩余有效期与「即将到期」预警
- 客户在手机上（375px）打开门户链接无横向滚动、可正常查看与下载

## 二、用户故事（User Stories）

### US-001 门户链接到期/即将到期 → 自动通知律师（后端，核心）
**描述：** 作为律师，我希望客户门户链接在即将到期与过期时自动收到系统通知，以便在链接失效前续期或提前联系客户。

**验收标准：**
- [ ] 每小时扫描任务 `scan_expired_portal_links_task`（`app/tasks/__init__.py:745`）在链接 active→expired 时创建通知给 `link.created_by`
- [ ] 同一任务对 3 天内将到期（`now < expires_at <= now+3d`）的 active 链接创建「即将到期」提醒
- [ ] 通知按 `scan_contract_expiry_alerts_task`（`app/tasks/__init__.py:783`）同款去重：`reference_type="portal_link"`, `reference_id=link.id`, 去重键存 body（`portal_link:{id}:expired` / `portal_link:{id}:expiring_soon`），重复运行不重复创建（幂等）
- [ ] 事件字段：`event_type="portal"`, `channel="site"`, `status="delivered"`（含 `sent_at`），必带 `organization_id` / `case_id` / `user_id`
- [ ] 通知标题含案件标题（无则回退 `案件#{case_id}`）
- [ ] 测试验证幂等性与两个触发条件
- [ ] 无需 DB schema 变更（去重键方案，迁移 head 不变）

### US-002 最小站内通知中心（通知的可见载体）
**描述：** 作为律师，我希望在顶部看到一个通知铃铛并点开查看未读系统提醒，以便第一时间处理门户到期等事项。

**验收标准：**
- [ ] 后端新增 `GET /api/developer/notifications/me` → `{items: 最近50条站内通知(serialize_event), unread: 未读数}`（`delivered`/`sent` 计为未读，前端过滤 `failed`）
- [ ] 后端新增 `POST /api/developer/notifications/{id}/read`（`mark_as_read`）与 `POST /api/developer/notifications/read-all`（`mark_all_as_read`）
- [ ] 前端新增 `NotificationBell.vue`（原生 button + CSS 角标 + 下拉列表，不新增 Element Plus 按需引入），挂在 `App.vue` 顶栏 `.topbar-actions`
- [ ] 前端新增 `api/notifications.js` 并注册到 `api/index.js`
- [ ] 刷新后未读状态以服务端为准、保持一致
- [ ] Typecheck 通过；浏览器验证（run skill）
- [ ] 已知限制可接受：顶栏在 ≤1280px 隐藏（与角色徽章一致），MVP 范围内

### US-003 管理端门户链接状态可见性
**描述：** 作为管理员/审核律师，我希望在客户门户链接列表中直接看到剩余有效期与「即将到期」预警，以便及时处理。

**验收标准：**
- [ ] `LegalPortalTab.vue` 状态列：active 且 ≤3 天 → warning 标签「即将到期」；active → success「生效中」；其余显示原状态
- [ ] Typecheck 通过；浏览器验证

### US-004 客户门户移动端最小适配
**描述：** 作为客户，我希望在手机上打开门户链接也能正常查看案件进展并下载文书。

**验收标准：**
- [ ] 375px 视口下 OTP 验证码输入框宽度自适应（`width:100%`，替换固定 `240px`）
- [ ] 发票摘要 `el-descriptions` 在窄屏不横向溢出（`table-layout:fixed` + `word-break`）
- [ ] 主内容（时间线/文档列表/反馈）375px 下无横向滚动
- [ ] 用 Playwright / run 在 375px 视口验证

## 三、功能需求（Functional Requirements）

- FR-1：每小时扫描任务在链接 active→expired 时创建「已到期」通知给创建者（幂等）
- FR-2：同一扫描任务对 3 天内到期的 active 链接创建「即将到期」通知（幂等）
- FR-3：通知事件复用 `LegalNotificationEvent`，字段规范见 US-001；`organization_id` 非空必传 `link.organization_id`
- FR-4：`GET /api/developer/notifications/me` 返回最近 50 条站内通知 + unread 数
- FR-5：`POST /api/developer/notifications/{id}/read` 单条已读
- FR-6：`POST /api/developer/notifications/read-all` 全部已读
- FR-7：顶栏通知铃铛（未读角标、下拉列表、全部标记已读）
- FR-8：管理端门户链接列表显示剩余天数 /「即将到期」标签
- FR-9：门户页 375px 移动端适配（OTP 输入、发票描述、无横向滚动）

## 四、非目标（Non-Goals）

- 门户访问行为分析入周报（P2）
- 品牌化扩展、多链接聚合页（P2）
- 客户可见账单对账（P3，#74）
- 飞书插件 M1；真实邮件/飞书投递（demo 以站内通知为准）
- 提前提醒天数配置化（固定 3 天）
- 修复既有 deadline/contract 通知事件仍为 pending、对铃铛不可见的问题（既有缺口，另行处理）
- 清理未调度的重复任务 `legal_scan_expired_portal_links_task`（`app/services/legal_scheduler.py:146`）
- access_limited（访问次数上限）触发通知

## 五、技术要点（Technical Considerations）

- 复用：`notification_service`、`LegalNotificationEvent`、`scan_contract_expiry_alerts_task` 去重模式
- 新通知事件直接以 `status="delivered"` 落库（`dispatch_pending` 未挂任何 beat 任务；`_deliver_site` 也只是标记 delivered）
- 新端点挂 `app/api/legal_platform_api.py`（router 前缀 `/api/developer`）
- 前端沿用手动按需引入 Element Plus 约定；铃铛用原生 button 规避引入链
- 测试沿用现有模式：unittest + sqlite + `get_db` override + patch redis（参考 `tests/test_legal_v3_portal.py`；`test_meeting_and_async_flows.py:688` 的 `SessionLocal` patch 模式）

## 六、涉及文件（Critical Files）

| 改动 | 路径 |
|---|---|
| 修改 | `app/tasks/__init__.py`（`scan_expired_portal_links_task` 加通知逻辑） |
| 修改 | `app/api/legal_platform_api.py`（新增 3 个通知端点） |
| 新增 | `frontend/src/components/legal/NotificationBell.vue` |
| 新增 | `frontend/src/api/notifications.js` |
| 修改 | `frontend/src/api/index.js`、`frontend/src/App.vue` |
| 修改 | `frontend/src/components/legal/LegalPortalTab.vue`（状态列） |
| 修改 | `frontend/src/views/LegalPortal.vue`（移动端） |
| 新增 | `tests/test_legal_portal_notification.py`、`tests/test_legal_notification_center.py` |

## 七、成功指标（Success Metrics）

- 同一链接重复运行扫描任务 0 条重复通知（幂等）
- 律师在链接到期前至少收到 1 条提醒，到期时收到 1 条（demo 中可见）
- 375px 视口门户页无横向滚动
- 铃铛未读数与已读操作正确联动

## 八、开放问题（Open Questions）

- 提前提醒天数（当前固定 3 天）是否需要组织/案件维度可配置？（MVP 不做）
- 到期通知是否要走邮件/飞书渠道？（当前 demo 站内即可，接入真实投递后再启用）
