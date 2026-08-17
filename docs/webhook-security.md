# P1-C Webhook 统一验证与防重放

所有**入站**第三方回调（电子签署、支付、飞书事件）统一经
`app/core/webhook_verifier.py` 的 `WebhookVerifier` 验签，禁止各路由自行拼接验签
逻辑。本文档分「已实现」与「部署方需确认」两节。

## 已实现

### 统一验签器 `WebhookVerifier`（app/core/webhook_verifier.py）

- **原始请求体签名**：对 `request.body()` 的原始字节做 HMAC-SHA256，**不重新序列化
  JSON**（防签名绕过）；`hmac.compare_digest` 常量时间比较。
- **时间戳窗口**：过期/未来超出 `WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS`（默认 300s）
  即拒绝（`EXPIRED`）；窗口参数可配置。
- **fail-closed**：未配置密钥 → `NOT_CONFIGURED` 直接拒绝，不静默放行。
- 错误标准化为 `WebhookVerificationError(code, message)`，消息与日志**不包含
  签名密钥**；验签错误码：`NOT_CONFIGURED / MISSING_FIELD / INVALID_SIGNATURE /
  EXPIRED`。

支持的签名方案（scheme）：

| scheme | 格式 | 时间戳 | 说明 |
|---|---|---|---|
| `raw` | `hex|base64(HMAC-SHA256(secret, body))` | 无 | 签署回调（fadada/esigncn） |
| `stripe` | 头 `t=<ts>,v1=<hmac>`，签串 `f"{ts}."+body` | 必需 | Stripe 兼容格式 |
| `feishu_v1` | `base64(HMAC-SHA256(secret, body))` | 无 | 飞书旧版 |
| `feishu_v2` | `base64(HMAC-SHA256(secret, f"{ts}{nonce}{secret}"+body))` | 必需 | 飞书推荐 |
| `feishu_auto` | 依次尝试 v2 → v1 → 旧 hex | 视分支 | 兼容既有部署 |

### nonce / 事件唯一 ID 去重（跨实例共享存储）

- 新增 `webhook_nonces` 表（alembic `20261201_0083`），`UNIQUE(namespace, nonce)`；
  `app/core/webhook_dedup.claim_nonce` 以 INSERT + 唯一约束原子去重（**数据库即共享
  存储，多实例部署下仍然有效**），并发重放同一 nonce 仅一个成功。
- nonce 过期按 `WEBHOOK_REPLAY_TTL_SECONDS`（默认 3600s）写入，写入路径惰性
  清理过期行，不阻塞请求。
- 事件唯一 ID 类方案（签署 `provider_event_id`、支付 `provider+event_id`）沿用
  既有数据库唯一约束，与 nonce 表互补。

### 接入的三个回调

1. **签署回调** `/api/legal/signing/webhooks/{provider}`：`raw` 方案验签；
   时间戳防重放由事件唯一 ID 承担；回调内 `occurred_at` 异常时序（乱序/未来时间/
   失败终态）检测沿用既有逻辑。
2. **支付回调** `/api/billing/subscriptions/webhook`：`stripe` 方案验签
   （`verify_signature` 内部改走统一验签器，对外错误码
   `WEBHOOK_SIGNATURE_NOT_CONFIGURED / INVALID_WEBHOOK_SIGNATURE /
   WEBHOOK_SIGNATURE_EXPIRED` 不变）；验签失败在 `handle_webhook` 写安全审计。
3. **飞书事件回调** `/api/feishu/callbacks/event`：改走统一验签器并
   **fail-closed**（未配置密钥即拒绝 + 审计，不再静默放行）；
   `url_verification` 握手指纹为平台明文 challenge，不经签名（回调配置流程）；
   `FEISHU_CALLBACK_VERIFY=off` 为**显式降级**（仅排查/开发），每次请求记录审计
   `WEBHOOK_VERIFICATION_DISABLED`；v2 模式下强制时间戳窗口 + nonce 去重。

### 安全审计

- 验签失败/降级写 `security_audit_events`（event_type=`webhook`，追加式哈希链）：
  只记录 provider、错误码、模式等元数据，**绝不包含签名密钥或完整敏感载荷**。
- 审计写失败按 `security_audit_service` 降级策略处理，不阻断验签拒绝本身。

### 幂等 / 时钟偏差 / 重试策略

- **幂等**：签署/支付以事件唯一 ID 的数据库唯一约束实现，重复回调返回既有结果且
  不重复产生副作用（支付侧另有 `idempotency_key`）；飞书以 nonce 去重。
- **时钟偏差**：时间窗默认 300s，可配置（`WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS` /
  `PAYMENT_WEBHOOK_SIGNATURE_TOLERANCE_SECONDS`）；要求部署方时钟与 NTP 对齐
  （见下）。
- **重试**：验签失败的回调不得重试放行——每次请求独立验签；发送方重试携带相同
  事件 ID/nonce 会被幂等/去重拦截，不产生副作用；支付事件处理失败由
  `PAYMENT_EVENT_MAX_ATTEMPTS` 指数退避重试（事件已落库）。

## 部署方需确认

1. **密钥配置**：签署（`SIGNING_WEBHOOK_SECRETS_JSON`）、支付
   （`PAYMENT_WEBHOOK_SECRET`）、飞书（`FEISHU_EVENT_ENCRYPT_KEY`）密钥由部署方
   注入（环境变量/Secret Manager），**未配置时相应回调 fail-closed 拒绝**。
2. **共享 nonce 存储**：`webhook_nonces` 表随迁移创建，无需额外组件（数据库即共享
   存储）；如部署在分库分表环境，需确认回调接入库一致。
3. **时钟同步**：所有验签时间戳与服务器时钟比较，部署方须启用 NTP/chrony，
   偏差大于时间窗会导致合法回调被误拒。
4. **飞书模式**：生产建议 `FEISHU_CALLBACK_VERIFY=v2`（含时间戳+nonce）。
   `auto` 兼容旧 hex 直签（仅迁移期使用）；`off` 禁止生产使用（显式降级 + 审计）。
5. **窗口与 TTL 调优**：`WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS` / 
   `WEBHOOK_REPLAY_TTL_SECONDS` 按业务与网络时延复核；TTL 过短会使合法重试
   （同一事件）被当作新事件重复处理，过长则 nonce 表膨胀（惰性清理兜底）。

## 测试覆盖（tests/test_webhook_verifier.py）

签名正确/错误/缺失、过期、重放（同 nonce 二次）、**并发重放**（两线程/两连接仅一
成功）、缺少字段、载荷篡改、未配置密钥 fail-closed、错误消息不含密钥、审计不含
密钥与完整载荷（API 层签署/飞书两条路径）。既有回归：
test_feishu_api / test_payment_webhook_reliability / test_subscription_phase10 /
test_legal_v3_signing。