"""Celery 任务与外部连接器可靠性设置。

全部为有限值：不允许无限重试、无限超时或无限 lock TTL。
"""

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings

from app.core.config.base import ENV_FILE_CONFIG


class ReliabilitySettings(BaseSettings):
    model_config = ENV_FILE_CONFIG

    # ── 队列拆分与路由 ──────────────────────────────────────────────
    TASK_QUEUE_ROUTING_ENABLED: bool = Field(default=True)
    TASK_DEFAULT_QUEUE: str = Field(default="connector")

    # ── 分布式锁 ────────────────────────────────────────────────────
    TASK_LOCK_DEFAULT_TTL_SECONDS: int = Field(default=300, ge=10, le=86400)
    TASK_LOCK_RENEW_INTERVAL_SECONDS: int = Field(default=60, ge=10, le=3600)

    # ── 外部调用韧性层 ──────────────────────────────────────────────
    EXTERNAL_DEFAULT_TIMEOUT_SECONDS: int = Field(default=10, ge=1, le=300)
    EXTERNAL_MAX_ATTEMPTS: int = Field(default=3, ge=1, le=10)
    EXTERNAL_MAX_WAIT_SECONDS: int = Field(default=30, ge=1, le=600)
    EXTERNAL_BACKOFF_BASE_SECONDS: float = Field(default=1.0, ge=0.1, le=60)
    EXTERNAL_BACKOFF_JITTER: bool = Field(default=True)
    EXTERNAL_RESPECT_RETRY_AFTER: bool = Field(default=True)
    EXTERNAL_RETRY_AFTER_MAX_SECONDS: int = Field(default=300, ge=1, le=3600)

    # 熔断（独立于 LLM 供应商熔断）：按 服务|连接器 隔离
    EXTERNAL_CIRCUIT_BREAKER_ENABLED: bool = Field(default=True)
    EXTERNAL_CIRCUIT_FAILURE_THRESHOLD: int = Field(default=5, ge=2, le=100)
    EXTERNAL_CIRCUIT_COOLDOWN_SECONDS: int = Field(default=60, ge=5, le=3600)
    EXTERNAL_CIRCUIT_HALF_OPEN_MAX_CONCURRENCY: int = Field(default=1, ge=1, le=10)

    # ── 任务运行台账 ────────────────────────────────────────────────
    TASK_RUNS_RECORD_TASKS: list[str] = Field(default_factory=lambda: [
        "parse_document",
        "document_chunk",
        "document_index",
        "summarize_document",
        "analyze_document",
        "process_open_contract_review",
        "parse_contract_versions",
        "connector_sync_task",
        "run_database_archive",
        "create_pilot_backup",
    ])
    TASK_RUNS_RETENTION_DAYS: int = Field(default=30, ge=1, le=365)

    # ── 连接器同步台账 ──────────────────────────────────────────────
    SYNC_DEFAULT_BATCH_SIZE: int = Field(default=100, ge=1, le=1000)
    SYNC_MAX_ATTEMPTS: int = Field(default=3, ge=1, le=10)
    SYNC_BACKOFF_BASE_SECONDS: int = Field(default=30, ge=5, le=3600)
    SYNC_RUN_LEASE_TTL_SECONDS: int = Field(default=900, ge=60, le=86400)
    # 默认关闭：同步框架仅以 mock 连接器接入，不产生真实外部调用。
    CONNECTOR_SYNC_ENABLED: bool = Field(default=False)
    CONNECTOR_SYNC_MOCK_MODE: bool = Field(default=True)

    # ── 幂等 ────────────────────────────────────────────────────────
    EMAIL_SEND_DETERMINISTIC_IDEMPOTENCY: bool = Field(default=True)

    # ── 通知/外发投递（Outbox 领取、租约、重试、死信）──────────────────
    # 站内低风险通知自动批准（分级审批策略的一部分）。
    AUTO_APPROVE_SITE_NOTIFICATION: bool = Field(default=True)
    # 发往内部用户本人邮箱的通知自动批准（可信渠道）；对外收件人走审批。
    AUTO_APPROVE_EMAIL_NOTIFICATION_TO_OWNER: bool = Field(default=True)
    # 对外邮件通知是否需要审批。默认偏安全：无法判定时不得自动外发。
    EMAIL_NOTIFICATION_REQUIRE_APPROVAL: bool = Field(default=True)
    # 通知事件领取批次与租约 TTL（worker 崩溃后按 TTL 回收重领）。
    NOTIFICATION_CLAIM_BATCH_SIZE: int = Field(default=50, ge=1, le=500)
    NOTIFICATION_CLAIM_TTL_SECONDS: int = Field(default=300, ge=60, le=86400)
    NOTIFICATION_MAX_ATTEMPTS: int = Field(default=5, ge=1, le=20)
    NOTIFICATION_BACKOFF_BASE_SECONDS: int = Field(default=60, ge=5, le=3600)
    # 邮件投递（EmailSendRequest 作为 Outbox）的重试与租约。
    EMAIL_DELIVERY_MAX_ATTEMPTS: int = Field(default=5, ge=1, le=20)
    EMAIL_DELIVERY_CLAIM_TTL_SECONDS: int = Field(default=300, ge=60, le=86400)
    EMAIL_DEAD_LETTER_RETENTION_DAYS: int = Field(default=90, ge=7, le=3650)

    # ── DLP 发送前硬门禁 ──────────────────────────────────────────────
    # 扫描器异常/未配置时的默认行为：block（fail closed）。对外邮件与敏感内容强制 block。
    DLP_SCAN_FAILURE_ACTION: str = Field(default="block", pattern="^(block|warn)$")
    DLP_SCANNER_VERSION: str = Field(default="rule-based-v1")
    # 显式运行模式：enabled（规则扫描器）/ disabled（未配置真实扫描器，不伪造通过，
    # 默认按 fail closed 阻断对外发送）。
    DLP_SCANNER_MODE: str = Field(default="enabled", pattern="^(enabled|disabled)$")

    # ── 邮箱同步（绿地，mock 连接器，默认关闭；不复活真实 IMAP 供应商）──
    MAILBOX_SYNC_ENABLED: bool = Field(default=False)
    MAILBOX_SYNC_BATCH_SIZE: int = Field(default=50, ge=1, le=1000)
    MAILBOX_ATTACHMENT_MAX_BYTES: int = Field(default=20 * 1024 * 1024, ge=1)
    MAILBOX_ATTACHMENT_ALLOWED_MIME_JSON: list[str] = Field(default_factory=lambda: [
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain",
    ])
    MAILBOX_ATTACHMENT_STORAGE_PREFIX: str = Field(default="mailbox-attachments")

    # ── 支付 Webhook 事件（验签 / 幂等 / 乱序 / 重放）──────────────────
    # fail-closed：开启时未配置 PAYMENT_WEBHOOK_SECRET 也拒绝事件（生产要求）。
    PAYMENT_WEBHOOK_REQUIRE_SIGNATURE: bool = Field(default=True)
    PAYMENT_WEBHOOK_SIGNATURE_TOLERANCE_SECONDS: int = Field(default=300, ge=60, le=3600)
    PAYMENT_EVENT_CLAIM_BATCH_SIZE: int = Field(default=50, ge=1, le=500)
    PAYMENT_EVENT_CLAIM_TTL_SECONDS: int = Field(default=300, ge=60, le=86400)
    PAYMENT_EVENT_MAX_ATTEMPTS: int = Field(default=5, ge=1, le=20)
    PAYMENT_EVENT_BACKOFF_BASE_SECONDS: int = Field(default=60, ge=5, le=3600)

    # ── 每日对账 ─────────────────────────────────────────────────────
    RECONCILIATION_STALE_PAYMENT_DAYS: int = Field(default=7, ge=1, le=365)
    RECONCILIATION_STALE_WEBHOOK_MINUTES: int = Field(default=60, ge=5, le=10080)
    RECONCILIATION_RUN_LEASE_TTL_SECONDS: int = Field(default=900, ge=60, le=86400)
    RECONCILIATION_MAX_DISCREPANCIES: int = Field(default=500, ge=10, le=10000)

    # ── P1-D SSRF 防护（出站 URL 目标校验，fail-closed）───────────────
    # 默认开启：拒绝回环/私网/链路本地/未指定/组播/保留地址与 localhost；
    # 显式关闭属降级（内网直连出站的部署场景），必须持续审计并记录于文档。
    SSRF_GUARD_ENABLED: bool = Field(default=True)

    @model_validator(mode="after")
    def _validate_finite(self) -> "ReliabilitySettings":
        if self.EXTERNAL_MAX_WAIT_SECONDS <= 0:
            raise ValueError("EXTERNAL_MAX_WAIT_SECONDS 必须为正")
        if self.TASK_LOCK_DEFAULT_TTL_SECONDS <= 0:
            raise ValueError("TASK_LOCK_DEFAULT_TTL_SECONDS 必须为正")
        return self
