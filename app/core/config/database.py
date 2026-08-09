"""Database / Redis / retention-archive settings."""

import json
from typing import Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings

from app.core.config.base import ENV_FILE_CONFIG


class DatabaseSettings(BaseSettings):
    model_config = ENV_FILE_CONFIG

    DATABASE_URL: str = "sqlite:///./data/app.db"
    DATABASE_POOL_PRE_PING: bool = True
    DATABASE_ECHO: bool = False
    # E-7：MySQL 连接池尺寸（pool_size + max_overflow = 上限）。
    # LLM 调用期间请求不持有连接后，pool_size 覆盖同时进行中的请求数即可；
    # 每个请求瞬时最多占用 2 个连接（主 session + LLM 用量记录 session）。
    DATABASE_POOL_SIZE: int = Field(default=20, ge=1, le=200)
    DATABASE_POOL_MAX_OVERFLOW: int = Field(default=40, ge=0, le=400)
    # 连接池回收 / checkout 超时（MySQL 8h wait_timeout 前回收，避免 stale 连接）。
    DATABASE_POOL_RECYCLE: int = Field(default=1800, ge=60, le=86400)
    DATABASE_POOL_TIMEOUT: int = Field(default=30, ge=1, le=300)
    # 慢 SQL 阈值（毫秒）；0 = 关闭慢 SQL 统计。仅记录语句前缀，不记录参数。
    DATABASE_SLOW_QUERY_MS: int = Field(default=500, ge=0, le=60000)
    DATABASE_MONITOR_ENABLED: bool = True

    REDIS_URL: str = "redis://localhost:6379/0"

    # 邮件留存（imap_mailbox 连接器）；与数据库归档保留策略同域管理。
    MAILBOX_RETENTION_DAYS: int = Field(default=90, ge=7, le=3650)

    # —— 大表归档 / 保留策略（见 app/services/archive_service.py）——
    # 默认关闭且 dry-run：开发环境永不真实删除；生产也须显式开启。
    DATABASE_ARCHIVE_ENABLED: bool = False
    DATABASE_ARCHIVE_DRY_RUN: bool = True
    DATABASE_ARCHIVE_BATCH_SIZE: int = Field(default=200, ge=50, le=5000)
    # 归档运行锁：超过该时长仍处于 running 视为陈旧，可被抢占。
    DATABASE_ARCHIVE_LOCK_TIMEOUT_MINUTES: int = Field(default=30, ge=5, le=1440)
    # 每表保留天数 JSON，如 {"operation_logs": 180, "token_usage": 365}；空对象 = 不归档任何表。
    DATABASE_ARCHIVE_RETENTION_DAYS_JSON: str = "{}"
    # 通用幂等键 TTL（秒）：过期后由清理任务删除，键可复用。
    IDEMPOTENCY_KEY_TTL_SECONDS: int = Field(default=86400, ge=60, le=2592000)

    @model_validator(mode="after")
    def validate_database_config(self) -> "DatabaseSettings":
        if not self.DATABASE_URL or self.DATABASE_URL == "sqlite:///./data/app.db":
            import warnings

            warnings.warn(
                "使用默认SQLite数据库，生产环境请配置MySQL/PostgreSQL",
                UserWarning,
            )
        return self

    @field_validator("DATABASE_ARCHIVE_RETENTION_DAYS_JSON")
    @classmethod
    def validate_retention_json(cls, v: str) -> str:
        if not v:
            return v
        try:
            mapping = json.loads(v)
        except json.JSONDecodeError as exc:
            raise ValueError("DATABASE_ARCHIVE_RETENTION_DAYS_JSON必须是有效的JSON对象") from exc
        if not isinstance(mapping, dict):
            raise ValueError("DATABASE_ARCHIVE_RETENTION_DAYS_JSON必须是对象")
        for table, days in mapping.items():
            if not isinstance(table, str) or not isinstance(days, int) or days <= 0:
                raise ValueError("DATABASE_ARCHIVE_RETENTION_DAYS_JSON的值必须是 表名->正整数")
        return v

    def archive_retention_days(self) -> dict[str, int]:
        """解析保留天数 JSON，返回 表名->天数 映射。"""
        if not self.DATABASE_ARCHIVE_RETENTION_DAYS_JSON:
            return {}
        parsed: Any = json.loads(self.DATABASE_ARCHIVE_RETENTION_DAYS_JSON)
        return {str(table): int(days) for table, days in parsed.items()}
