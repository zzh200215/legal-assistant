# 试点账号交接清单（#44，启动基线 2026-08-04）

> 用途：试点启动日把账号包发给 10 家律所 + 首日护航核对表。
> 初始密码：统一 `Pilot@2026`（交接时单独发送，**本清单不含密码**）；
> 要求各所首次登录后修改密码。
> 基线快照：`data/pilot-baseline-20260804.json`（试点启动前全口径，周报对比起点）。

## 1. 账号表（10 家所 × 2 账号）

| 所 | 组织 code | 律师账号（dept_admin） | 助理账号（user） |
|---|---|---|---|
| 试点律所01 | pilot01 | pilot01-lawyer | pilot01-assistant |
| 试点律所02 | pilot02 | pilot02-lawyer | pilot02-assistant |
| 试点律所03 | pilot03 | pilot03-lawyer | pilot03-assistant |
| 试点律所04 | pilot04 | pilot04-lawyer | pilot04-assistant |
| 试点律所05 | pilot05 | pilot05-lawyer | pilot05-assistant |
| 试点律所06 | pilot06 | pilot06-lawyer | pilot06-assistant |
| 试点律所07 | pilot07 | pilot07-lawyer | pilot07-assistant |
| 试点律所08 | pilot08 | pilot08-lawyer | pilot08-assistant |
| 试点律所09 | pilot09 | pilot09-lawyer | pilot09-assistant |
| 试点律所10 | pilot10 | pilot10-lawyer | pilot10-assistant |

- 角色：lawyer = 律所管理员（可审核、可看成员）；assistant = 普通成员（咨询/审查/文书）。
- 团队版配额：咨询 5000 / 合同审查 2000 / 文书 2000 次/月（`PLAN_QUOTAS`），配额在 `user_subscriptions` 已挂载。
- 每家所组织间数据隔离（独立 organization_id，成员经 `organization_members` 绑定）。

## 2. 登录与支持

- 登录地址：试点环境 URL（内部 staging 为 `http://127.0.0.1:8001`，对外地址随部署发布）。
- 支持渠道：内部群 + 每日巡检（`scripts/pilot_daily_check.py`）与周报（`docs/pilot-weekly-sample.md` 口径）。
- 演示脚本：`docs/p4-demo-and-interview.md`；运营话术/7 天目标：`docs/pilot-success-playbook.md`。

## 3. 7 天目标（pilot-success-playbook.md 对齐）

每家所在 7 天内完成 ≥1 个「案件闭环」：建案 → 咨询 → （审查/文书）→ 律师审核通过 → 计费记录。

## 4. 启动基线（2026-08-04 快照摘要）

- 用户 29（非管理员）、组织 10、案件 0、咨询 6、审查 3、文书 2、审核决策 0
- 30 天 LLM 成本：¥3.78（qwen-plus 为主；咨询 ¥2.17 / 审查 ¥1.17 / 文书 ¥0.28）
- 回流游标：`scripts/review_feedback_state.json` last_action_id=0
- 半成品开关（支付/签署/开放 API）：保持关闭

## 5. 首日护航核对表

- [ ] 每家所 2 账号能登录（lawyer + assistant）
- [ ] 各所完成首条咨询（漏斗 first_consultation +1/所）
- [ ] 发现的问题进当日巡检日志，P0 走 ALERT_WEBHOOK
- [ ] 次日核对：基线快照增量（重跑 `scripts/snapshot_pilot_baseline.py` 对比）
