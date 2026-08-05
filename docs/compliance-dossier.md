# 等保/DPA 合规材料（#75，2026-09-01）

> 销售/客户成功可出示的合规清单。每条对应**代码级实现**（非纸面承诺），
> 供企业 POC 的等保/DPA 审计问答与合同附件引用。

## 1. 数据加密（AES-256-GCM）

| 项 | 实现 | 位置 |
|---|---|---|
| 字段级加密 | `EncryptedText` TypeDecorator（AES-256-GCM，独立密钥 `LEGAL_DATA_ENCRYPTION_KEY`，密文带版本前缀 `_v1:`） | app/core/encryption.py:62 |
| 覆盖范围 | 案件客户姓名/对方当事人/案情摘要、咨询内容、审查内容、文书内容、合同正文/快照、客户联系方式、平台密文 | models/legal.py、legal_contract.py、legal_billing.py、legal_platform.py |
| 密文入库 | 上述模型字段全部以密文存储，纯文本快照与原文件分离 | legal_contract.py:44 |

## 2. PII 脱敏（出 LLM 前 + 出站 DLP）

| 项 | 实现 | 位置 |
|---|---|---|
| 规则引擎 | 身份证/银行卡/手机/邮箱/API 令牌/密码字段 6 类规则检测+掩码 | services/data_protection_service.py |
| LLM 出站脱敏 | `_redact_for_llm`：咨询/审查/文书主链路调用 LLM 前统一脱敏，返回脱敏标记 | services/legal_service.py:292 |
| 出站邮件 DLP | 邮件发送前 inspect → should_block（按策略阻断/告警），动作入操作日志 | services/outbound_email_service.py |

## 3. 审计与操作日志（双轨）

| 轨 | 说明 | 位置 |
|---|---|---|
| 安全审计 | admin_audit_logs：管理/高风险操作留痕 | models/auth_log.py |
| 操作日志 | operation_logs + oplog_service：模块级操作全覆盖（发送、DLP 阻断、计费等） | models/operation_log.py |

## 4. Webhook 签名（HMAC-SHA256）

- webhook 回调校验：`hmac.new(secret, payload, sha256)` + `compare_digest`（app/tasks/__init__.py:870、legal_contract_api.py:687）
- 订阅回调同模式（subscription_api.py）

## 5. 资源级权限矩阵

| 机制 | 说明 |
|---|---|
| verify_resource_access | 资源级鉴权：document_access_rules 显式访问规则 + 组织/角色层级（admin>reviewer>editor>client） |
| 角色层级 | auth.py 角色枚举：admin/reviewer/editor/client 四级，接口级 `required_roles` 校验 |
| 门户隔离 | 客户门户 OTP 令牌 + 5 次锁定 + 资源可见性按组织隔离（portal 模块） |

## 6. 其他可出示项

- 密钥管理：加密密钥独立于 SECRET_KEY 配置；CI 用占位密钥、生产用真实密钥（conftest 不回退覆盖）
- 试点半成品开关：支付/签署/开放 API 默认关闭（E-2b 门禁）
- 数据生命周期：备份任务（每日 02:00 全量）、周报口径含成本/留存审计

## 7. 待补（POC 审计问答前）

1. 数据保留/删除策略（用户注销后数据处置流程）——✅ SLA 草案 docs/data-retention-sla-draft.md + 注销流程已实现（#95/0061）
2. 隐私政策与用户协议文本（法务出）——✅ 草案 docs/privacy-policy-and-user-agreement-draft.md，待法务确认
3. 供应商清单（LLM 供应商 qwen + 数据出境说明）——✅ 草案 docs/supplier-list-and-data-transfer-draft.md（主链路 dashscope 境内不涉出境），待法务书面确认
4. 等保测评（等保二级起步）——✅ POC 阶段自评 docs/etc-protection-poc-self-assessment.md（含制度文本：docs/security-policy-compilation-draft.md + docs/emergency-response-plan-draft.md），正式商用前委托测评

## 8. 关联

- #67 企业 POC 销售包 §2 锁定点 1（数据合规）
- #69 checklist B 组"等保/DPA 合规材料整理成册"（本报告即交付物）
