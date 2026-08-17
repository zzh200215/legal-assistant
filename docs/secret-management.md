# 密钥管理与轮换（P1-A）

> 范围：统一密钥读取抽象（SecretProvider）、密钥版本化、受控轮换流程与轮换审计。
> 不覆盖：LLM 出站数据保护（P0，见 `docs/llm-outbound-data-protection.md`）、
> 数据库静态加密列的选择（见 `app/core/encryption.py` 与 `docs/security-policy-compilation-draft.md` §6）。
> 关联配置：`docs/CONFIG.md`「安全密钥配置」；实现：`app/core/secrets/`、
> `app/core/encryption.py`、`scripts/rotate_encryption_key.py`。

## 已实现

以下能力已在代码中实现并有自动化测试覆盖（`tests/test_secret_provider.py`、
`tests/test_key_rotation_audit.py`、`tests/test_legal_encryption.py`）：

### 1. 统一 SecretProvider 接口

- `app/core/secrets/base.py`：`SecretProvider` 抽象接口（`get` / `get_version` /
  `list_versions` / `current_version` / `rotation_state`），`KeyVersion` 与
  `RotationState` 数据类（版本、激活标记、状态：active / pending_retirement / retired）。
- **环境变量实现** `EnvSecretProvider`（默认）：语义与既有环境变量完全兼容
  （`LEGAL_DATA_ENCRYPTION_KEY` 单密钥 = `v1`；`LEGAL_DATA_ENCRYPTION_KEYS_JSON`
  多版本密钥环 + `LEGAL_DATA_ENCRYPTION_ACTIVE_VERSION`）；普通密钥（如
  `LLM_API_KEY`）按名称直读 settings 字段。
- **KMS/Secret Manager 适配器骨架** `KmsSecretProvider`：**未接入任何真实云 KMS**。
  未配置 `SECRET_KMS_REGION` / `SECRET_KMS_ENDPOINT` 时构造即抛
  `SecretProviderNotConfiguredError`；配置后所有接口仍显式拒绝——绝不伪造"已接入"。
- 工厂 `get_secret_provider()`：按 `SECRET_PROVIDER=env|kms` 选择，默认 `env`；
  `kms` 未配置时不静默回退 env（避免掩盖配置错误）。

### 2. 密钥版本化与旧数据可解密

- 密文携带版本：`enc:{version}:{nonce+ciphertext}`；`EncryptedText` 列、`encrypt_text` /
  `decrypt_text`、MFA 密钥、平台 webhook 密文统一走该机制。
- 双密钥过渡窗口：轮换后旧版本密钥保留在环中，旧密文持续可解密；
  **仅当确认全量重加密完成、且无残留后**才允许受控摘除。
- 缺失版本/缺失密钥 → `SecretNotFoundError`（fail-closed）；版本存在但密钥错误或
  密文被篡改 → `SecretDecryptionError`（fail-closed），**异常消息不含密钥材料**。

### 3. 受控轮换流程

- `scripts/rotate_encryption_key.py`：
  - `--dry-run`（版本分布，无需密钥）、`--verify`（全量可解密校验）、
    `--new-key`（生成新版本 + 全量重加密 + 输出需写入 .env 的值，**不修改 .env**）；
  - `--retire <version>` **受控摘除**：四道门禁（版本存在 / 非激活版本 / 全表可解密 /
    无该版本密文残留），任一不满足即拒绝并返回失败原因（`app/core/secrets/rotation.py`）。
- 轮换/校验/摘除各阶段写 `security_audit_events`（`event_type=key_rotation`，
  追加式 + 哈希链），**只记录版本号/行数等元数据，绝不记录密钥原文**
  （`app/core/secrets/audit.py`；审计写失败降级告警，不静默吞错）。

### 4. 安全默认与约束

- 本地开发默认 `env` 提供方，无需额外基础设施；生产未配置 KMS 时行为与现状一致
  （env 密钥环），不会悄悄改变密钥来源。
- 密钥不硬编码、不写日志、不返回客户端：配置脱敏沿用 `SENSITIVE_FIELDS` +
  `redacted_dict()`；审计只存元数据；异常消息不含密钥。

## 部署方需确认

以下为运行环境责任，**尚未在代码中启用或无法由代码保证**，接入/上线前必须确认：

- **真实云 KMS/Secret Manager 接入**：代码中的 `KmsSecretProvider` 是**骨架**，
  未调用任何云 SDK。如启用，需由部署方在 `app/core/secrets/kms_provider.py` 中
  按云 SDK 实现四个接口、补充测试与凭据配置（`SECRET_KMS_REGION` /
  `SECRET_KMS_ENDPOINT` / `SECRET_KMS_PREFIX` 及云侧权限），并完成切换演练。
- **密钥托管与轮换排程**：生产密钥环（`LEGAL_DATA_ENCRYPTION_KEYS_JSON`）的
  保管（KMS/保险库）、轮换周期与责任人、轮换窗口（低峰期 + 备份）由部署方制定；
  建议轮换前执行 `scripts/create_pilot_backup.py`。
- **.env 写入与发布**：脚本只输出 `env_to_set`，由运维写入 .env 并滚动重启进程；
  多实例部署需确保所有实例在摘除旧版本前已切换到新版本。
- **审计保留期限**：`security_audit_events`（含 key_rotation 事件）的保留/归档
  期限与访问控制，与 `docs/data-retention-sla-draft.md` 对齐。
- **连接器凭据密钥**：`CONNECTOR_CREDENTIAL_ENCRYPTION_KEY` 目前仍为环境变量直读
  （为空时从 SECRET_KEY 派生，属降级路径）；统一迁入 SecretProvider 列为后续项。
- **LLM/第三方凭据**：`LLM_API_KEY` 等目前保持环境变量直读（满足"不硬编码、
  不进日志、不出客户端"底线）；迁入 provider 属可选增强，未在本次范围。

## 后续 P1 待办（不在本次范围）

- 真实云 KMS 接入实现与测试（阿里云 KMS / AWS Secrets Manager 等）。
- 连接器凭据 / LLM 密钥统一迁入 SecretProvider。
- 密钥自动轮换调度（定时任务 + 重加密批处理）。
- 依赖扫描与 SBOM。
