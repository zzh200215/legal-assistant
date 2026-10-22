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

## 3. 一键退回（要求补充依据）

通知铃铛与通知中心里，「无依据咨询待审核」这条通知带一个按钮「要求补充依据」：

- 点击 → `POST /legal/review-queue/consultation/{id}/actions`，`action=return`、
  `note="无依据咨询：请补充法源依据后重新提交审核"`；
- 成功 → 通知标记已读、按钮消失、提示「已退回，要求补充依据」；
- 失败（非审核律师 403、状态不允许 400）→ 直接显示错误，不改本地状态；
- 权限仍由服务端校验：按钮只是入口，不是安全边界；
- 通知中心与铃铛行为一致；通知中心的点击跳转也补上了 `tab=consultation&consultation_id=`。

## 4. 幂等与容错

- 复用 `notification_service.create_notification` 的幂等键（组织 + 用户 + 渠道 + 事件 + 引用 + 版本），重复提交不会刷屏；
- 咨询表没有 `version` 列，所以同一条咨询对同一收件人**只会发一次**；
- 通知失败不回滚审核提交：`try/except` 只记日志（`logger.exception`），主流程优先。

## 5. 验证

| 项 | 结果 |
| --- | --- |
| 后端 | 无引用咨询提交审核 → 审核律师收到 1 条通知（标题/正文/引用 id 正确），提交人收不到；通知中心接口能查到；有引用的咨询提交 → 不产生通知；审核律师对无依据咨询 `return` → 200、状态 `returned_for_facts`、历史里有带备注的 return |
| e2e（u3） | 铃铛显示未读 → 打开面板看到「无依据咨询待审核」→ 点「要求补充依据」→ 断言请求体 `{action:"return", note:"无依据咨询：请补充法源依据后重新提交审核"}`、提示出现、按钮消失 → 再点通知跳转 URL 带 `tab=consultation&consultation_id=989` |
| 门禁 | 见提交说明 |

## 6. 边界

1. **只发一次**：同一咨询对同一审核律师只提醒一次（没有 version 维度），退回后重新提交不会再次提醒。
2. **不检查免打扰/偏好**：`create_notification` 只做站内事件落库，用户的通知偏好（静音时段、渠道）目前不作用于站内通知。
3. **只通知组织审核律师**：没有组织时无人可通知；也不通知案件客户。
4. **只覆盖「无引用」**：`weak`（有引用但覆盖低）不发通知，只在审核台打标签。
5. **通知是追加的**：不撤回、不改写；咨询后来补了引用，旧通知仍在（已读状态不变）。
6. **一键退回只针对这一种通知**：标题/引用类型匹配才显示按钮；其它通知没有内联动作。
7. **退回后不自动重新通知**：提交人补完依据重新提交时，如果仍然无引用会再发一次（幂等键限制为同一咨询一次，见 §2）；实际重提不会重复提醒，需人工在审核台确认。
