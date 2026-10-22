# 第三十一步交付说明：无依据咨询的站内通知

> 一句话：提交一条没有法源引用的咨询时，同组织的审核律师会收到一条站内通知「无依据咨询待审核」，点开直达该咨询的结果卡——不用等到审核台才发现。

- 日期：2026-10-22
- 后端：`app/services/legal/legal_workspace_service.py`（`apply_review_action` 的 submit_review 分支 + `_notify_evidence_missing`）
- 前端：无改动（通知铃铛/通知中心已有，点击跳转逻辑本就支持 `reference_type=consultation`）
- 测试：`tests/test_legal_review_assignment.py`（+2 例）、`frontend/tests/e2e/u3-workflow.spec.js`

---

## 1. 触发规则

| 条件 | 说明 |
| --- | --- |
| 动作是 `submit_review` | 提交审核时触发，不是创建草稿时 |
| 对象是 `consultation` | 合同/文书不触发 |
| `evidence_summary.level === "none"` | 与 step29 同一口径（没有 references） |
| 提交人属于某个组织 | 无组织（个人工作台）不发 |

**收件人**：同组织 `legal_role in (admin, reviewer)` 的成员，排除提交人本人。

**内容**：
- 标题：`无依据咨询待审核`
- 正文：`「<问题前 40 字>」没有关联法源引用，审核前请要求补充依据或直接退回。`
- 关联：`reference_type=consultation`、`reference_id=<咨询 id>`、`case_id`；事件类型复用 `approval`（走已有通知偏好/列表过滤）。

## 2. 点击行为

通知铃铛 → 点击该条 → `markNotificationRead` → 路由到 `/legal-workspace?case_id=..&tab=consultation&consultation_id=..`，咨询 tab 自动恢复该条结果卡（引用/句级溯源/忠实度一目了然）。

## 3. 幂等与容错

- 复用 `notification_service.create_notification` 的幂等键（组织 + 用户 + 渠道 + 事件 + 引用 + 版本），重复提交不会刷屏；
- 咨询表没有 `version` 列，所以同一条咨询对同一收件人**只会发一次**；
- 通知失败不回滚审核提交：`try/except` 只记日志（`logger.exception`），主流程优先。

## 4. 验证

| 项 | 结果 |
| --- | --- |
| 后端 | 无引用咨询提交审核 → 审核律师收到 1 条通知（标题/正文/引用 id 正确），提交人收不到；通知中心接口能查到；有引用的咨询提交 → 不产生通知 |
| e2e（u3） | 铃铛显示未读 → 打开面板看到「无依据咨询待审核」→ 点击后 URL 带 `tab=consultation&consultation_id=989` |
| 门禁 | 见提交说明 |

## 5. 边界

1. **只发一次**：同一咨询对同一审核律师只提醒一次（没有 version 维度），退回后重新提交不会再次提醒。
2. **不检查免打扰/偏好**：`create_notification` 只做站内事件落库，用户的通知偏好（静音时段、渠道）目前不作用于站内通知。
3. **只通知组织审核律师**：没有组织时无人可通知；也不通知案件客户。
4. **只覆盖「无引用」**：`weak`（有引用但覆盖低）不发通知，只在审核台打标签。
5. **通知是追加的**：不撤回、不改写；咨询后来补了引用，旧通知仍在（已读状态不变）。
