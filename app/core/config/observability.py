"""Observability settings: Sentry / OTel / structured log / alert webhook."""

from pydantic import Field
from pydantic_settings import BaseSettings

from app.core.config.base import ENV_FILE_CONFIG


class ObservabilitySettings(BaseSettings):
    model_config = ENV_FILE_CONFIG

    # E-5 可观测性：Sentry 错误上报 + OpenTelemetry 链路追踪（留空/关闭时跳过初始化）
    SENTRY_DSN: str = ""
    OTEL_ENABLED: bool = False
    OTEL_EXPORTER_OTLP_ENDPOINT: str = ""
    # 等保差距 #2：开启后双轨日志（操作/审计/登录）以 JSON 行输出到 audit.json 日志，
    # 供集中日志/SIEM 汇聚；关闭（默认）时零开销。
    STRUCTURED_LOG_JSON_LINES: bool = False
    # STRUCTURED_LOG_JSON_LINES 开启时的落盘文件（SIEM 采集端），目录不存在会自动创建。
    STRUCTURED_LOG_FILE: str = "data/logs/audit.jsonl"

    # 运营告警 webhook
    ALERT_WEBHOOK_URL: str = ""
    ALERT_WEBHOOK_TIMEOUT_SECONDS: int = Field(default=5, ge=1, le=30)
    ALERT_WEBHOOK_MIN_SEVERITY: str = "high"
