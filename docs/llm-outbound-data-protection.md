# LLM 出站数据保护与可审计性（P0）

> 范围：仅覆盖**出站 LLM 请求**（应用 → 外部模型提供方）的数据保护与审计。
> 不覆盖：数据库静态加密（见 `app/core/encryption.py` 与 `docs/security-policy-compilation-draft.md` §6）、
> 外发邮件 DLP（见 `app/services/notification/dlp_scanner.py`）、文件上传安全、密钥轮换、Webhook 防重放
> （列为后续 P1 待办，见文末）。
> 关联配置：`docs/CONFIG.md`「LLM 出站数据保护（P0）」；实现：`app/services/llm/llm_outbound_gate.py`、
> `app/core/data_levels.py`。

## 已实现

以下能力已在代码中实现并有自动化测试覆盖（`tests/test_data_levels.py`、
`tests/test_llm_outbound_gate.py`、`tests/test_data_protection_service.py`）：

### 1. 统一数据分级模型

- 集中枚举 `DataLevel`：`public` / `internal` / `sensitive` / `highly_sensitive`
  （`app/core/data_levels.py`），带等级排序与解析；业务代码禁止散落字符串判断。
- action 基础等级默认映射（可被 `LLM_OUTBOUND_ACTION_DATA_LEVEL_JSON` 覆盖）：
  - `embedding → public`；
  - `chat / chat_stream / generate / generate_with_images / rag_* → internal`；
  - `legal_* / document_* / meeting_* / email_* / task_* / agent_* → sensitive`；
  - **未知 action → sensitive（deny-by-default）**。
- 内容升级：命中任何 PII 规则 → 至少 `sensitive`；命中 high/critical 严重度规则
  （身份证号/银行卡号/访问令牌/密码字段）→ `highly_sensitive`。

### 2. 统一出站安全网关（唯一入口，无旁路）

所有外部 LLM 调用（chat / generate / structured_generate（含结构化修复子请求）/
generate_with_images / chat_stream / embed）均收敛于 `ModelGateway`
（`app/core/llm_client.py`），网关在每个公开方法内、构建供应商载荷**之前**调用
`LLMOutboundGate.guard`（`app/services/llm/llm_outbound_gate.py`）：

- **PII 检测**：`data_protection_service` 规则扫描器（手机号/身份证号/邮箱/银行卡号/
  访问令牌/密码字段/**中文地址（保守形态）**）。
- **默认脱敏后才允许发送**：命中且未达极敏感的内容，发往供应商的是脱敏文本
  （chat 消息、prompt、embedding 文本逐片段脱敏；图像 URL 不参与文本扫描）。
- **极敏感默认拦截**：`highly_sensitive` 内容默认禁止发送，仅
  `LLM_OUTBOUND_HIGHLY_SENSITIVE_ACTIONS_JSON` 显式放行名单内的 action 允许，
  且仍先脱敏。
- **检测故障 fail closed**：规则检测异常时默认阻断**全部**出站请求
  （`LLM_OUTBOUND_DLP_FAILURE_ACTION=block`）并记录原因；`warn` 仅为逃生通道
  （放行并记录，不保证 PII 不外泄，文档中明确标注）。
- **未命中 PII 的正常请求行为不变**：无命中时文本原样透传，接口返回结构与业务语义不变。
- 拦截错误为稳定业务错误：HTTP 403，`code=LLM_OUTBOUND_DATA_BLOCKED`，
  detail 含 action/data_level/reason。

### 3. 结构化审计日志（不含原始 PII）

`llm_call_logs`（迁移 `20261110_0082`）新增：`provider`、`data_level`、
`pii_hit_codes`（仅规则 code 的 JSON 数组）、`pii_hit_count`、`redacted_count`、
`blocked_reason`。审计字段覆盖：时间（`created_at`）、请求 ID（`request_id`）、
用户/租户标识（`user_id` / `organization_id`，来自认证上下文与链路上下文）、
数据等级、命中规则、脱敏数量、是否拦截、目标模型/提供方（`model_name` / `provider`）。

- 审计日志**不保存原始 PII、完整提示词或密钥**：请求/响应摘录沿用
  `observability_sanitizer`（敏感 action 全量元数据化，其余截断），且摘录取自
  **脱敏后**文本；`pii_hit_codes` 只存规则标识。
- 审计开关：`LLM_OUTBOUND_AUDIT_ENABLED`（关闭时跳过 DLP 专有字段，基础审计照旧）。

### 4. 配置与安全默认值

- PII 检测开关、审计开关、极敏感放行策略、action 分级覆盖、检测故障动作均从
  既有 pydantic-settings 配置体系读取（`app/core/config/llm.py` + `.env`）。
- 本地开发安全默认：检测与审计默认开启；放行名单默认空（全部拦截）；
  故障默认 block。不把密钥/真实个人信息/生产 URL 写入代码。

## 部署方需确认

以下为运行环境责任，**尚未在代码中启用或无法由代码保证**，部署/上线前必须确认：

- **KMS/密钥托管**：`LEGAL_DATA_ENCRYPTION_KEY` 及其轮换版本
  （`LEGAL_DATA_ENCRYPTION_KEYS_JSON`）的生产托管方式（KMS/保险库）、
  轮换周期与责任人是部署方职责；出站网关本身不管理该密钥。
- **日志保留期限**：`llm_call_logs` 的保留/归档/清理期限（与
  `docs/data-retention-sla-draft.md` 对齐），以及审计导出的访问控制，需部署方配置。
- **地区/跨境传输**：本网关不改变模型提供方与调用地域。若部署环境或供应商位于
  其他法域，数据传输合规（如出境评估/标准合同条款）由部署方与供应商协议确认。
- **第三方模型数据政策**：脱敏后仍可能包含业务上下文；第三方模型供应商的数据
  使用/训练政策需在合同层面约束（供应商清单见 `docs/supplier-list-and-data-transfer-draft.md`）。
- **人工审批流程**：`highly_sensitive` 放行名单（`LLM_OUTBOUND_HIGHLY_SENSITIVE_ACTIONS_JSON`）
  的评审与审批流程、放行后的定期复核由部署方管理；代码默认不放行任何 action。
- **检测器误报/漏报调优**：规则正则（含地址规则）的误报率监控与调优、以及
  命中规则清单变更时的回归测试，需部署方建立运营流程。
- **故障逃生通道**：`LLM_OUTBOUND_DLP_FAILURE_ACTION=warn` 会放行未检测内容，
  仅建议在受控窗口内使用，且需在变更评审中记录。

## 后续 P1 待办（不在本次范围）

- 密钥与轮换（KMS 集成、密钥版本自动轮换）。
- 文件上传安全（内容检测、存储加密策略、下载审计）。
- Webhook 防重放与签名体系加固。
- 依赖扫描与 SBOM。
