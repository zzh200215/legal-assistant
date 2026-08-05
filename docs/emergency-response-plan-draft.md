# 应急预案（草案，待管理确认）# 合规 5.3 交付

> 用途：等保二级自评差距项 #3 的成文交付物之一（docs/etc-protection-poc-self-assessment.md §2.9/§3 #3）。
> 承接《安全管理制度汇编》第 11 章；处置流程对齐当前代码实现（告警/降级/备份恢复），
> 标注【管理确认】处需业务/法务复核。
> 关联：docs/security-policy-compilation-draft.md、docs/operations-runbook.md、docs/dr-drill-record.md。

## 1. 目的与范围

- 目的：对平台故障、安全事件、供应商异常做到"早发现、快响应、可恢复、有复盘"。
- 范围：律智检 SaaS 应用层与数据面；云基础设施故障按云服务商 SLA 联动。

## 2. 应急组织与职责

| 角色 | 职责 | 备注 |
|---|---|---|
| 应急负责人 | 定级、决策、对外沟通 | 默认平台运营者【管理确认】 |
| 值班工程师 | 监测、初判、处置 | 告警值班（E-5 告警通道） |
| 数据/安全专员 | 数据恢复、安全事件处置 | 备份恢复 / 账号安全 |
| 客户沟通 | 通知受影响客户 | 门户公告 + 邮件 |

## 3. 事件分级

| 级别 | 定义 | 示例 | 响应时限 |
|---|---|---|---|
| P1（重大） | 服务不可用/数据泄露/资损 | 主库故障、大范围账号盗用 | ≤15 分钟响应，持续处置 |
| P2（严重） | 核心功能降级 | LLM 供应商故障、计费异常 | ≤30 分钟响应 |
| P3（一般） | 局部/轻微 | 单用户异常、告警误报 | ≤24 小时 |

## 4. 监测与告警（现状）

- 告警通道：ALERT_WEBHOOK_URL（企业微信/钉钉/飞书 webhook），按严重度分级（ALERT_WEBHOOK_MIN_SEVERITY）。
- 周期巡检任务：beat 心跳（aibg:operations:beat:last_tick）、操作告警 dispatch（300s）、审批超时（300s）、订阅过期（3600s）、发票逾期（3600s）、门户链接过期（3600s）。
- LLM 治理：速率限制 + 日额度 + 路由失败率告警（LLM_ROUTING_ALERT_PRIMARY_FAILURE_RATE）。
- 成本告警：按动作成本核算，超阈值触发（AI-5/E-5）。

## 5. 事件处置流程

### 5.1 LLM 供应商故障（dashscope）

- 症状：咨询/审查/文书超时或失败率上升；额度欠费。
- 处置：
  1. 确认 LLM_API_KEY/余额与限流日志；
  2. 启用降级：小模型路由（LLM_SMALL_MODEL）→ 本地 Ollama（OLLAMA_BASE_URL）→ 兜底提示"服务暂不可用"；
  3. 命中阈值自动告警；P2 处置；欠费则联系充值并按 AI-6 决策复测。
- 恢复：供应商恢复后按路由健康自动回主链路。

### 5.2 数据库故障与备份恢复

- 症状：接口 500/连接池耗尽；主库不可达。
- 处置：
  1. 确认数据库连接池（DATABASE_POOL_SIZE）与慢查询；
  2. 若数据损坏/丢失：用最新备份恢复（每日 02:00 全量，scripts/create_pilot_backup.py，SHA256 校验 manifest）；
  3. 恢复**仅在隔离环境**进行并记录恢复结果（备份 manifest.restore_requirement）；
  4. 复盘根因并登记到容灾演练记录（docs/dr-drill-record.md）。
- 备注：增量备份/异地副本为等保差距 #1，正式商用前补齐后恢复窗口缩短。

### 5.3 安全事件（账号盗用 / 数据泄露 / 异常访问）

- 症状：异常登录 IP、账号锁定告警、门户 OTP 异常、审计日志异常。
- 处置：
  1. 按登录失败锁定/审计日志定位来源；
  2. 立即禁用受影响账号（账号禁用 + 注销流程）；
  3. 若涉数据泄露：按 PIPL 第 57 条评估告知义务【法务确认】，评估是否通知监管与当事人；
  4. 留存证据链（admin_audit_logs + operation_logs）供调查。

### 5.4 支付 / 计费异常

- 症状：订阅过期未降级、发票状态异常、对账不符。
- 处置：
  1. 核对订阅状态机（active→expired→降级 free）；
  2. 发票/收款对账查 legal_billing（LegalInvoice + LegalPaymentRecord），门户账单快照对照；
  3. 异常单走作废（void）+ 冲正（refund）流程。

### 5.5 Redis / 缓存故障

- 症状：会话/限流/门户 OTP 失效；beat 心跳中断。
- 处置：Redis 重启或切换；限流与配额治理临时放宽；OTP 门户临时降级为人工核验。

### 5.6 备份任务异常

- 症状：每日 02:00 备份任务返回 error（subprocess 失败）。
- 处置：检查 mysqldump/pg_dump 与磁盘空间，重跑 `python scripts/create_pilot_backup.py --confirm`，确认 SHA256 校验通过。

## 6. 通信与上报

- 内部：P1 立即拉群；P2 值班通报；处置过程记录到事件台账【管理确认】。
- 客户：服务不可用超 30 分钟 → 门户公告 + 邮件通知话术（模板待成文【管理确认】）。
- 监管：涉数据泄露等法定情形按 PIPL 第 57 条/等保要求上报【法务确认】。

## 7. 事后复盘

- 每次 P1/P2 事件出复盘报告：时间线、根因、处置、改进项。
- 复盘结论并入周报口径（scripts/pilot_weekly_report.py）与月度合规评审。

## 8. 演练计划

- 备份恢复演练：每季度一次（与 docs/dr-drill-record.md 联动）。
- 安全事件桌面演练：每半年一次【管理确认】。
- LLM 供应商故障降级演练：随升级发布演练。

## 9. 关联

- docs/security-policy-compilation-draft.md（制度汇编）
- docs/operations-runbook.md（运行手册）
- docs/dr-drill-record.md（容灾演练记录）
- docs/etc-protection-poc-self-assessment.md §3 #3（差距整改项）
