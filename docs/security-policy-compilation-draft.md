# 安全管理制度汇编（草案，待管理/法务确认）# 合规 5.3 交付

> 用途：等保二级自评（docs/etc-protection-poc-self-assessment.md §2.6-2.9）差距项 #3 的成文交付物之一。
> 依据 GB/T 22239-2019《网络安全等级保护基本要求》第二级与 GB/T 28448-2019《测评要求》整理，
> 每条制度对应**代码级实现**（非纸面承诺），标注【法务/管理确认】处需复核后定稿。
> 关联：docs/emergency-response-plan-draft.md（应急响应衔接）、docs/data-retention-sla-draft.md（数据分类）。

## 1. 总则

- 目的：规范平台安全运营、建设与管理，落实等保二级与《个人信息保护法》《数据安全法》要求。
- 适用范围：律智检 SaaS 平台（应用 + 数据面）；云基础设施物理/网络责任由云服务商承担（见等保自评 §1）。
- 制度构成：本汇编 + 《应急预案》+ 数据保留 SLA + 合规四件套（隐私政策/用户协议/供应商清单/数据保留）。

## 2. 信息安全组织与职责

| 制度 | 要求 | 现状实现 |
|---|---|---|
| 安全负责人 | 明确安全负责人并任命【管理确认】 | 待成文任命文件 |
| 角色分离 | 系统管理 / 审计管理 / 安全管理分离 | 平台角色四级（admin/reviewer/editor/client）+ 资源级鉴权（app/core/auth.py） |
| 职责分工 | 开发/运维/数据岗位职责界定 | 运行手册 docs/operations-runbook.md 覆盖运维职责 |

## 3. 人员与账号管理

| 制度 | 要求 | 现状实现 |
|---|---|---|
| 账号最小权限 | 按岗授权，最小化 | `required_roles` 接口级校验 + `verify_resource_access` 资源级校验 |
| 离岗账号回收 | 离岗即禁用/删除账号 | 账号注销 30 天冷却期 + 匿名化（account_deletion_service）；管理员可禁用成员 |
| 登录口令管理 | 复杂度校验 + 加密存储 | 密码 bcrypt 哈希存储（app/core/auth.py） |
| 登录失败处理 | 防暴力破解 | 5 次失败锁定 30 分钟（LOGIN_MAX_FAIL_COUNT/LOGIN_LOCK_DURATION_MINUTES） |

## 4. 访问控制与身份鉴别

| 制度 | 要求 | 现状实现 |
|---|---|---|
| 身份鉴别 | 传输加密 + 强鉴别 | 全站 HTTPS/TLS；JWT 令牌（ACCESS_TOKEN_EXPIRE_MINUTES）；客户门户 OTP 一次性验证码 + 5 次锁定 |
| 访问控制 | 主体-客体-操作授权 | RBAC 四级 + document_access_rules 显式规则 + 门户按组织隔离 |
| 会话管理 | 会话失效与登出 | JWT 过期 + 门户 X-Portal-Session 会话 |

## 5. 密码与密钥管理

| 制度 | 要求 | 现状实现 |
|---|---|---|
| 密钥分级 | 主密钥与业务密钥分离 | 加密密钥 `LEGAL_DATA_ENCRYPTION_KEY` 独立于 SECRET_KEY；轮换支持 `LEGAL_DATA_ENCRYPTION_KEYS_JSON` |
| 密钥存储 | 不落源码/日志 | CI 用占位密钥、生产用真实密钥（conftest 不回退覆盖） |
| 密钥轮换 | 定期轮换【管理确认】 | 支持密文版本前缀 `_v1:` 的多版本轮换机制 |

## 6. 数据安全管理制度

| 制度 | 要求 | 现状实现 |
|---|---|---|
| 数据分类分级 | 分类分级与标识 | 数据分类见 data-retention-sla-draft §1（A-F 六类 + 期限） |
| 静态加密 | 敏感字段加密 | EncryptedText（AES-256-GCM）覆盖案件/咨询/审查/文书/合同/客户联系/平台密文 |
| 出站防护 | 防敏感信息外泄 | LLM 出站 PII 脱敏（data_protection_service）、邮件 DLP（outbound_email_service） |
| 供应商管理 | 供应商数据处理约束 | 供应商清单 docs/supplier-list-and-data-transfer-draft.md；dashscope 境内处理不涉及出境 |
| 数据删除 | 注销后处置 | 30 天冷却期 + 匿名化状态机（#95 已实现） |

## 7. 网络安全与区域边界

| 制度 | 要求 | 现状实现 |
|---|---|---|
| 通信加密 | 传输全程加密 | 全站 HTTPS/TLS |
| 边界防护 | 防火墙/安全组 | 云侧由部署方配置（继承阿里云基线） |
| 风险管控 | 非法请求/滥用防护 | LLM 速率限制 + 日额度治理（LLM_DAILY_REQUEST_LIMIT）+ 429 友好提示 |

## 8. 软件开发与上线管理

| 制度 | 要求 | 现状实现 |
|---|---|---|
| 开发安全 | 上线前检查 | CI 四门禁：pytest 全量、ruff（E9/F821/F823/F632/F706/F811）、OpenAPI 契约检查、评测回归（E-6） |
| 变更管理 | 发布评审与回退 | git 分支 + 提交规范；迁移文件 alembic 版本化 |
| 供应链安全 | 依赖与第三方 | 依赖锁定（requirements/package-lock）；第三方组件见供应商清单 |

## 9. 日志与审计管理

| 制度 | 要求 | 现状实现 |
|---|---|---|
| 审计留痕 | 管理/高风险操作留痕 | admin_audit_logs（安全审计）+ operation_logs（模块级操作）双轨 |
| 日志留存 | 留存时限 | 180 天活跃 + 归档 2 年（等保 ≥180 天要求）；见 SLA §1-D |
| 日志保护 | 防篡改/掩码 | 日志追加写；敏感字段掩码策略见 SLA |

## 10. 备份与恢复管理

| 制度 | 要求 | 现状实现 |
|---|---|---|
| 定期备份 | 每日全量 | celery beat 每日 02:00 全量备份（app/tasks.create_pilot_backup_task → scripts/create_pilot_backup.py），MySQL/PostgreSQL 逻辑备份 + 数据目录打包 + SHA256 校验 |
| 恢复验证 | 定期恢复演练 | 容灾演练记录 docs/dr-drill-record.md；备份恢复要求"仅隔离环境恢复并记录结果" |
| 异地容灾 | 异地副本【差距】 | 见等保自评 §3 #1：增量备份 + 异地副本待正式商用前补齐 |

## 11. 应急响应衔接

- 本汇编事件处置部分由《应急预案》承接（监测、分级、处置流程、通信、复盘），见 docs/emergency-response-plan-draft.md。

## 12. 合规与培训

- 等保二级正式测评：POC 阶段自评，正式商用前委托测评机构（等保自评 §3 #5）。
- 安全意识培训：每半年一次【管理确认】，纳入入职培训。
- 隐私政策/用户协议：草案已出，待法务确认后对外发布（compliance-dossier §7.2）。

## 13. 制度发布与修订

- 本汇编每年至少评审一次；发生重大变更（架构、法规）时随时修订。
- 修订版经版本号 + 发布日期管理，历史版本留存。

## 14. 关联

- docs/etc-protection-poc-self-assessment.md §2.6-2.9、§3 #3
- docs/emergency-response-plan-draft.md
- docs/data-retention-sla-draft.md
