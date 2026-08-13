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

    @model_validator(mode="after")
    def _validate_finite(self) -> "ReliabilitySettings":
        if self.EXTERNAL_MAX_WAIT_SECONDS <= 0:
            raise ValueError("EXTERNAL_MAX_WAIT_SECONDS 必须为正")
        if self.TASK_LOCK_DEFAULT_TTL_SECONDS <= 0:
            raise ValueError("TASK_LOCK_DEFAULT_TTL_SECONDS 必须为正")
        return self
