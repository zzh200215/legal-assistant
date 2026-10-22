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
| `evidence_summary.level in ("none", "weak")` | 与 step29 同一口径：无引用（none）或句级覆盖 <50%/忠实度未通过（weak） |
| 提交人属于某个组织 | 无组织（个人工作台）不发 |

**收件人**：同组织 `legal_role in (admin, reviewer)` 的成员，排除提交人本人。

**内容**：

| 等级 | 标题 | 正文 | template_key |
| --- | --- | --- | --- |
| none | 无依据咨询待审核 | 「问题前 40 字」没有关联法源引用，审核前请要求补充依据或直接退回。 | `review_consultation_uncited` |
| weak | 依据偏弱的咨询待审核 | 「问题前 40 字」法源依据偏弱（句级溯源 2/10 句），审核前请留意依据是否充分。 | `review_consultation_weak` |

关联：`reference_type=consultation`、`reference_id=<咨询 id>`、`case_id`；事件类型复用 `approval`。`template_key` 是前端按钮的稳定标记（标题可能改，键不改）。

## 2. 点击行为

通知铃铛 → 点击该条 → `markNotificationRead` → 路由到 `/legal-workspace?case_id=..&tab=consultation&consultation_id=..`，咨询 tab 自动恢复该条结果卡（引用/句级溯源/忠实度一目了然）。

## 3. 一键退回（要求补充依据）

通知铃铛与通知中心里，「无依据咨询待审核」这条通知带一个按钮「要求补充依据」：

- 点击 → `POST /legal/review-queue/consultation/{id}/actions`，`action=return`，note 按等级：
  - 无依据：`无依据咨询：请补充法源依据后重新提交审核`
  - 依据偏弱：`依据偏弱：请补充法源依据后重新提交审核`；
- 成功 → 通知标记已读、按钮消失、提示「已退回，要求补充依据」；
- 失败（非审核律师 403、状态不允许 400）→ 直接显示错误，不改本地状态；
- 权限仍由服务端校验：按钮只是入口，不是安全边界；
- 通知中心与铃铛行为一致；通知中心的点击跳转也补上了 `tab=consultation&consultation_id=`。

## 4. 反向通知：退回后通知提交人

`return` 动作（审核台或通知里的一键退回）会给**提交人**发一条站内通知：

| 被退回咨询 | 标题 | 正文 |
| --- | --- | --- |
| 无引用（level=none） | 咨询被退回：需要补充依据 | 「问题前 40 字」没有关联法源引用，已被退回，请补充依据后重新提交。 |
| 有引用 | 咨询被退回，请查看审核意见 | 「问题前 40 字」被退回：<审核意见前 80 字> |

- 收件人 = `consultation.user_id`；自己退回自己的不通知；
- 点击同样直达该咨询结果卡（`tab=consultation&consultation_id=`）；
- 失败只记日志，不影响退回本身。

## 5. 「去补充」直达（预填追问框）

提交人收到的反向通知（`template_key=consultation_returned_supplement`）带「去补充」按钮：

- 点击/直接点通知 → `/legal-workspace?case_id=..&tab=consultation&consultation_id=..&supplement=1`；
- 咨询 tab 打开该条结果卡，把追问框预填为「请补充相关法源依据后重新回答上述问题。」并聚焦；
- 提交人点「追问」即可基于原咨询补充材料重新生成（新回答带新的引用/溯源）。

## 6. 幂等与容错

- 复用 `notification_service.create_notification` 的幂等键（组织 + 用户 + 渠道 + 事件 + 引用 + 版本），重复提交不会刷屏；
- 咨询表没有 `version` 列，所以同一条咨询对同一收件人**只会发一次**；
- 通知失败不回滚审核提交：`try/except` 只记日志（`logger.exception`），主流程优先。

## 7. 验证

| 项 | 结果 |
| --- | --- |
| 后端 | 无引用咨询提交审核 → 审核律师收到 1 条通知（标题/正文/引用 id/template_key 正确），提交人收不到；通知中心接口能查到；**依据偏弱**（2/10 句）提交 → 标题「依据偏弱的咨询待审核」、template_key=weak、正文含句级覆盖；有引用且覆盖正常的咨询提交 → 不产生通知；审核律师对无依据咨询 `return` → 200、`returned_for_facts`、历史含带备注的 return，提交人收到「咨询被退回：需要补充依据」（template_key=supplement）、审核律师本人不收；有引用的咨询被退回 → 提交人收到「咨询被退回，请查看审核意见」 |
| e2e（u3） | 铃铛显示未读 → 打开面板看到「无依据咨询待审核」→ 点「要求补充依据」→ 断言请求体、提示出现、按钮消失 → 点通知跳转 `consultation_id=989`；再打开铃铛点反向通知「去补充」→ URL 带 `supplement=1`，追问框已预填「请补充相关法源依据后重新回答上述问题。」 |
| 门禁 | 见提交说明 |

## 8. 边界

1. **只发一次**：同一咨询对同一审核律师只提醒一次（没有 version 维度），退回后重新提交不会再次提醒。
2. **不检查免打扰/偏好**：`create_notification` 只做站内事件落库，用户的通知偏好（静音时段、渠道）目前不作用于站内通知。
3. **只通知组织审核律师**：没有组织时无人可通知；也不通知案件客户。
4. **`weak` 也会通知，但文案不同**：都带「要求补充依据」，note 前缀区分（无依据 / 依据偏弱）；`partial/strong` 不通知。
5. **通知是追加的**：不撤回、不改写；咨询后来补了引用，旧通知仍在（已读状态不变）。
6. **一键退回只针对这一种通知**：标题/引用类型匹配才显示按钮；其它通知没有内联动作。
7. **每条咨询只发一次**：正向（提交）与反向（退回）通知都受幂等键限制（咨询无 version 维度）；同一咨询被多次退回，提交人只会收到第一次的反向通知。
8. **反向通知不区分退回原因**：除「无依据」外都用通用文案 + 审核意见，不会判断「是否真的缺依据」。
9. **「去补充」只是预填追问，不改原咨询**：补充走 followup（新回答），原咨询的 references/attribution 不会被修改。
