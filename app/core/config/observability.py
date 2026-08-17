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

    # ── 统一可观测性（P1）：上下文 / access log / 采样 / 预聚合 / SLO ──────
    # 是否开启 access 日志（structured_observe log_type="access"；默认关，开启才有开销）。
    OBS_ACCESS_LOG_ENABLED: bool = False
    # HTTP 入口接受/生成的请求 ID 头名。外部传入的 ID 仅按格式白名单校验，
    # 不信任外部身份字段（user_id/org_id 一律来自已认证上下文）。
    OBS_REQUEST_ID_HEADER: str = "X-Request-Id"
    # 关联上下文采样率（0-1）：日志/审计仍全量，仅 trace 相关字段按采样生成。
    OBS_CONTEXT_SAMPLE_RATE: float = Field(default=1.0, ge=0.0, le=1.0)
    # 详细模型日志开关：关闭（默认）时 model 类日志只记稳定摘要/用量/状态。
    OBS_MODEL_DETAIL_LOG_ENABLED: bool = False
    # metrics 快照落库开关：进程内指标按聚合窗口快照到 ops_metric_* 表。
    OBS_METRICS_SNAPSHOT_ENABLED: bool = True
    OBS_METRICS_SNAPSHOT_WINDOW_SECONDS: int = Field(default=300, ge=60, le=3600)

    # 预聚合（ops_metric_hourly/daily）：增量聚合窗口与保留天数。
    OBS_AGGREGATION_ENABLED: bool = True
    OBS_AGGREGATION_BATCH_SIZE: int = Field(default=500, ge=50, le=5000)
    # 小时/天预聚合保留天数（天级覆盖 SLO 与运营报表；小时级覆盖近 7 天）。
    OBS_AGGREGATION_HOURLY_RETENTION_DAYS: int = Field(default=7, ge=1, le=90)
    OBS_AGGREGATION_DAILY_RETENTION_DAYS: int = Field(default=90, ge=1, le=730)

    # SLO 建议目标（仅配置/口径，不假装已建外部告警平台）。
    SLO_API_P95_MS: int = Field(default=1500, ge=0, le=600000)
    SLO_LLM_SUCCESS_RATE: float = Field(default=0.95, ge=0.0, le=1.0)
    SLO_DOC_PARSE_RATE: float = Field(default=0.98, ge=0.0, le=1.0)
    SLO_AGENT_COMPLETION_RATE: float = Field(default=0.9, ge=0.0, le=1.0)
    SLO_NOTIFICATION_DELIVERY_RATE: float = Field(default=0.95, ge=0.0, le=1.0)

    # 审计保留期限（天）：按 retention_class 生效，归档/清理任务据此执行。
    OBS_AUDIT_RETENTION_DAYS_JSON: str = (
        '{"default": 180, "security": 365, "compliance": 730, "transient": 30}'
    )
    # 审计表 → retention_class 映射（表名 → default/security/compliance/transient）。
    OBS_AUDIT_TABLE_RETENTION_CLASS_JSON: str = (
        '{"admin_audit_logs": "default", "security_audit_events": "security", "login_logs": "transient"}'
    )
    # 审计默认不可直接物理删除：开启后过期审计行先流式归档到 OBS_AUDIT_ARCHIVE_DIR（JSONL + 清单），
    # 仅在 OBS_AUDIT_PURGE_AFTER_ARCHIVE=true 时才删除已归档行；关闭（默认）时过期审计行保留不清理。
    OBS_AUDIT_ARCHIVE_ENABLED: bool = False
    OBS_AUDIT_PURGE_AFTER_ARCHIVE: bool = False
    OBS_AUDIT_ARCHIVE_DIR: str = "data/archives/audit"
    # 审计写失败策略：default=degrade（记录降级日志并返回 None，不吞错）；
    # block 事件类（JSON 数组）写失败时抛 AuditWriteError，调用方按 fail-closed 处理。
    OBS_AUDIT_FAILURE_DEFAULT_ACTION: str = "degrade"
    OBS_AUDIT_FAILURE_BLOCK_EVENT_TYPES_JSON: str = (
        '["export", "permission_change", "sign_callback"]'
    )
    # 任务积压口径：task_runs 中 running/retrying 且 updated_at 早于该阈值视为
    # claimed-but-expired（worker 崩溃未回收），与 Redis Broker LLEN 分开记录。
    OBS_BACKLOG_STALE_MINUTES: int = Field(default=30, ge=5, le=1440)
    # 进程内指标快照保留天数（归档任务按此清理 ops_metric_snapshots）。
    OBS_METRICS_SNAPSHOT_RETENTION_DAYS: int = Field(default=30, ge=7, le=365)
    # WS 事件日志（断线恢复源）保留天数：resume 窗口 24h，超期行可物理清理。
    OBS_WS_EVENT_RETENTION_DAYS: int = Field(default=7, ge=1, le=90)
    # Agent 完成率 SLO：cancelled 是否计入分母（默认排除）。
    OBS_AGENT_SLO_EXCLUDE_CANCELLED: bool = True
