"""P1 可观测性·审计·运营分析：链路关联列 / 通用审计扩展 / 预聚合表。

- 链路关联列（统一上下文经 Celery headers 传播后落库，全部可空、存量行零迁移成本）：
  task_runs.request_id/agent_run_id；llm_call_logs.trace_id/task_id/agent_run_id/
  organization_id/error_category；legal_notification_events.trace_id/request_id；
  email_send_requests.trace_id/request_id；connector_sync_jobs.trace_id。
- security_audit_events 扩展为 P1 通用审计（schema_version=1 存量行继续按旧公式校验，
  新行 schema_version=2 纳入 action/resource/trace 字段参与哈希）。
- 审计表追加 archived_at（保留任务归档标记；默认不物理删除）。
- legal_async_jobs.input_json/output_json：审计导出任务条件与产物元数据。
- 新表 ops_metric_snapshots / ops_metric_hourly / ops_metric_daily / ops_metric_watermarks：
  进程内指标快照 + 幂等预聚合（金额一律 Numeric，禁止 float）。

Revision ID: 20260920_0080
Revises: 20260814_0078
Create Date: 2026-09-20
"""
from alembic import op
import sqlalchemy as sa

revision = "20260920_0080"
down_revision = "20260814_0078"
branch_labels = None
depends_on = None


def _extend_task_runs() -> None:
    with op.batch_alter_table("task_runs") as batch_op:
        batch_op.add_column(sa.Column("request_id", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("agent_run_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_task_runs_request_id", ["request_id"])
        batch_op.create_index("ix_task_runs_agent_run_id", ["agent_run_id"])


def _extend_llm_call_logs() -> None:
    with op.batch_alter_table("llm_call_logs") as batch_op:
        batch_op.add_column(sa.Column("trace_id", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("task_id", sa.String(128), nullable=True))
        batch_op.add_column(sa.Column("agent_run_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("organization_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("error_category", sa.String(32), nullable=True))
        batch_op.create_index("ix_llm_call_logs_trace_id", ["trace_id"])
        batch_op.create_index("ix_llm_call_logs_task_id", ["task_id"])
        batch_op.create_index("ix_llm_call_logs_agent_run_id", ["agent_run_id"])
        batch_op.create_index("ix_llm_call_logs_organization_id", ["organization_id"])
        batch_op.create_index("ix_llm_call_logs_error_category", ["error_category"])


def _extend_notification_events() -> None:
    with op.batch_alter_table("legal_notification_events") as batch_op:
        batch_op.add_column(sa.Column("trace_id", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("request_id", sa.String(64), nullable=True))
        batch_op.create_index("ix_legal_notification_events_trace_id", ["trace_id"])
        batch_op.create_index("ix_legal_notification_events_request_id", ["request_id"])


def _extend_email_send_requests() -> None:
    with op.batch_alter_table("email_send_requests") as batch_op:
        batch_op.add_column(sa.Column("trace_id", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("request_id", sa.String(64), nullable=True))
        batch_op.create_index("ix_email_send_requests_trace_id", ["trace_id"])
        batch_op.create_index("ix_email_send_requests_request_id", ["request_id"])


def _extend_connector_sync_jobs() -> None:
    with op.batch_alter_table("connector_sync_jobs") as batch_op:
        batch_op.add_column(sa.Column("trace_id", sa.String(64), nullable=True))
        batch_op.create_index("ix_connector_sync_jobs_trace_id", ["trace_id"])


def _extend_security_audit_events() -> None:
    with op.batch_alter_table("security_audit_events") as batch_op:
        batch_op.add_column(sa.Column("audit_id", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("action", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("resource_version", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("request_id", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("trace_id", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("task_id", sa.String(128), nullable=True))
        batch_op.add_column(sa.Column("agent_run_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("decision", sa.String(16), nullable=True))
        batch_op.add_column(sa.Column("reason_code", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("sanitized_metadata", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"))
        batch_op.add_column(sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index("ix_security_audit_events_audit_id", ["audit_id"])
        batch_op.create_index("ix_security_audit_events_trace_id", ["trace_id"])
        batch_op.create_index("ix_security_audit_events_request_id", ["request_id"])
        batch_op.create_index("ix_security_audit_events_task_id", ["task_id"])
        batch_op.create_index("ix_security_audit_events_agent_run_id", ["agent_run_id"])
        batch_op.create_index("ix_security_audit_events_archived_at", ["archived_at"])


def _extend_audit_archive_marks() -> None:
    for table in ("login_logs", "admin_audit_logs"):
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
            batch_op.create_index(f"ix_{table}_archived_at", ["archived_at"])


def _extend_legal_async_jobs() -> None:
    with op.batch_alter_table("legal_async_jobs") as batch_op:
        batch_op.add_column(sa.Column("input_json", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("output_json", sa.Text(), nullable=True))


def _create_ops_metric_tables() -> None:
    op.create_table(
        "ops_metric_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metric_name", sa.String(128), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False, server_default="counter"),
        sa.Column("labels_json", sa.Text(), nullable=True),
        sa.Column("count", sa.Numeric(20, 6), nullable=False, server_default="0"),
        sa.Column("sum_value", sa.Numeric(20, 6), nullable=True),
        sa.Column("p95_value", sa.Numeric(20, 6), nullable=True),
        sa.Column("numerator", sa.Numeric(20, 6), nullable=True),
        sa.Column("denominator", sa.Numeric(20, 6), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.UniqueConstraint(
            "bucket_start", "metric_name", "org_id", "kind", "labels_json",
            name="uq_ops_metric_snapshots_bucket",
        ),
    )
    op.create_index("ix_ops_metric_snapshots_bucket_start", "ops_metric_snapshots", ["bucket_start"])
    op.create_index("ix_ops_metric_snapshots_metric_name", "ops_metric_snapshots", ["metric_name"])
    op.create_index("ix_ops_metric_snapshots_org_id", "ops_metric_snapshots", ["org_id"])

    op.create_table(
        "ops_metric_hourly",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metric_name", sa.String(128), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=True),
        sa.Column("labels_json", sa.Text(), nullable=True),
        sa.Column("count", sa.Numeric(20, 6), nullable=False, server_default="0"),
        sa.Column("sum_value", sa.Numeric(20, 6), nullable=True),
        sa.Column("max_value", sa.Numeric(20, 6), nullable=True),
        sa.Column("p95_value", sa.Numeric(20, 6), nullable=True),
        sa.Column("numerator", sa.Numeric(20, 6), nullable=True),
        sa.Column("denominator", sa.Numeric(20, 6), nullable=True),
        sa.Column("cost_value", sa.Numeric(20, 6), nullable=True),
        sa.Column("source_watermark", sa.String(128), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.UniqueConstraint(
            "bucket_start", "metric_name", "org_id", "labels_json",
            name="uq_ops_metric_hourly_bucket",
        ),
    )
    op.create_index("ix_ops_metric_hourly_bucket_start", "ops_metric_hourly", ["bucket_start"])
    op.create_index("ix_ops_metric_hourly_metric_name", "ops_metric_hourly", ["metric_name"])
    op.create_index("ix_ops_metric_hourly_org_id", "ops_metric_hourly", ["org_id"])

    op.create_table(
        "ops_metric_daily",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metric_name", sa.String(128), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=True),
        sa.Column("labels_json", sa.Text(), nullable=True),
        sa.Column("count", sa.Numeric(20, 6), nullable=False, server_default="0"),
        sa.Column("sum_value", sa.Numeric(20, 6), nullable=True),
        sa.Column("max_value", sa.Numeric(20, 6), nullable=True),
        sa.Column("p95_value", sa.Numeric(20, 6), nullable=True),
        sa.Column("numerator", sa.Numeric(20, 6), nullable=True),
        sa.Column("denominator", sa.Numeric(20, 6), nullable=True),
        sa.Column("cost_value", sa.Numeric(20, 6), nullable=True),
        sa.Column("source_watermark", sa.String(128), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.UniqueConstraint(
            "bucket_start", "metric_name", "org_id", "labels_json",
            name="uq_ops_metric_daily_bucket",
        ),
    )
    op.create_index("ix_ops_metric_daily_bucket_start", "ops_metric_daily", ["bucket_start"])
    op.create_index("ix_ops_metric_daily_metric_name", "ops_metric_daily", ["metric_name"])
    op.create_index("ix_ops_metric_daily_org_id", "ops_metric_daily", ["org_id"])

    op.create_table(
        "ops_metric_watermarks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("granularity", sa.String(8), nullable=False),
        sa.Column("metric_name", sa.String(128), nullable=False),
        sa.Column("last_bucket", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.UniqueConstraint("granularity", "metric_name", name="uq_ops_metric_watermarks_key"),
    )


def upgrade() -> None:
    _extend_task_runs()
    _extend_llm_call_logs()
    _extend_notification_events()
    _extend_email_send_requests()
    _extend_connector_sync_jobs()
    _extend_security_audit_events()
    _extend_audit_archive_marks()
    _extend_legal_async_jobs()
    _create_ops_metric_tables()


def downgrade() -> None:
    op.drop_table("ops_metric_watermarks")
    op.drop_table("ops_metric_daily")
    op.drop_table("ops_metric_hourly")
    op.drop_table("ops_metric_snapshots")
    with op.batch_alter_table("legal_async_jobs") as batch_op:
        batch_op.drop_column("output_json")
        batch_op.drop_column("input_json")
    for table in ("login_logs", "admin_audit_logs"):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_index(f"ix_{table}_archived_at")
            batch_op.drop_column("archived_at")
    with op.batch_alter_table("security_audit_events") as batch_op:
        batch_op.drop_index("ix_security_audit_events_archived_at")
        batch_op.drop_index("ix_security_audit_events_agent_run_id")
        batch_op.drop_index("ix_security_audit_events_task_id")
        batch_op.drop_index("ix_security_audit_events_request_id")
        batch_op.drop_index("ix_security_audit_events_trace_id")
        batch_op.drop_index("ix_security_audit_events_audit_id")
        batch_op.drop_column("archived_at")
        batch_op.drop_column("schema_version")
        batch_op.drop_column("sanitized_metadata")
        batch_op.drop_column("reason_code")
        batch_op.drop_column("decision")
        batch_op.drop_column("agent_run_id")
        batch_op.drop_column("task_id")
        batch_op.drop_column("trace_id")
        batch_op.drop_column("request_id")
        batch_op.drop_column("resource_version")
        batch_op.drop_column("action")
        batch_op.drop_column("audit_id")
    with op.batch_alter_table("connector_sync_jobs") as batch_op:
        batch_op.drop_index("ix_connector_sync_jobs_trace_id")
        batch_op.drop_column("trace_id")
    with op.batch_alter_table("email_send_requests") as batch_op:
        batch_op.drop_index("ix_email_send_requests_request_id")
        batch_op.drop_index("ix_email_send_requests_trace_id")
        batch_op.drop_column("request_id")
        batch_op.drop_column("trace_id")
    with op.batch_alter_table("legal_notification_events") as batch_op:
        batch_op.drop_index("ix_legal_notification_events_request_id")
        batch_op.drop_index("ix_legal_notification_events_trace_id")
        batch_op.drop_column("request_id")
        batch_op.drop_column("trace_id")
    with op.batch_alter_table("llm_call_logs") as batch_op:
        batch_op.drop_index("ix_llm_call_logs_error_category")
        batch_op.drop_index("ix_llm_call_logs_organization_id")
        batch_op.drop_index("ix_llm_call_logs_agent_run_id")
        batch_op.drop_index("ix_llm_call_logs_task_id")
        batch_op.drop_index("ix_llm_call_logs_trace_id")
        batch_op.drop_column("error_category")
        batch_op.drop_column("organization_id")
        batch_op.drop_column("agent_run_id")
        batch_op.drop_column("task_id")
        batch_op.drop_column("trace_id")
    with op.batch_alter_table("task_runs") as batch_op:
        batch_op.drop_index("ix_task_runs_agent_run_id")
        batch_op.drop_index("ix_task_runs_request_id")
        batch_op.drop_column("agent_run_id")
        batch_op.drop_column("request_id")
