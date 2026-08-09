"""基础设施：数据库归档台账表 + 高频查询复合索引 + 业务唯一约束 + 子表外键级联。

按审计出的高频查询/增长表补索引；唯一约束前先清理存量脏数据；
外键 ON DELETE CASCADE 仅对 MySQL 生效（SQLite 默认不强制外键，应用层 ORM 已级联）。

Revision ID: 20260809_0067
Revises: 20260808_0066
Create Date: 2026-08-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "20260809_0067"
down_revision = "20260808_0066"
branch_labels = None
depends_on = None


def _dedupe_keep_latest(table: str, columns: list[str]) -> None:
    """删除重复业务键（保留每组最新 id），供随后添加唯一约束。MySQL/SQLite 均兼容。"""
    group_by = ", ".join(columns)
    op.execute(
        text(
            f"DELETE FROM {table} WHERE id NOT IN ("
            f"SELECT keep.id FROM (SELECT MAX(id) AS id FROM {table} GROUP BY {group_by}) keep)"
        )
    )


def _fk_constraint_names(table: str, column: str) -> list[str]:
    """MySQL 下发现指向该列的现有外键约束名（用于替换为 CASCADE）。"""
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return []
    rows = bind.execute(
        text(
            "SELECT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c "
            "AND REFERENCED_TABLE_NAME IS NOT NULL"
        ),
        {"t": table, "c": column},
    ).fetchall()
    return [row[0] for row in rows]


def _replace_fk_with_cascade(table: str, column: str, referent: str) -> None:
    """把既有外键替换为 ON DELETE CASCADE。

    SQLite 默认不强制外键、且旧外键无约束名无法按名删除，由 ORM 级联承担，
    此处跳过避免 batch 重建引入重复外键。
    """
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    for name in _fk_constraint_names(table, column):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_constraint(name, type_="foreignkey")
    with op.batch_alter_table(table) as batch_op:
        batch_op.create_foreign_key(
            f"fk_{table}_{column}_cascade", referent, [column], ["id"], ondelete="CASCADE",
        )


def upgrade() -> None:
    # 1) 归档台账表（锁 / 幂等 / 审计）
    op.create_table(
        "database_archive_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("table_name", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("batch_size", sa.Integer(), nullable=False, server_default=sa.text("200")),
        sa.Column("processed_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("deleted_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_database_archive_runs_table_name", "database_archive_runs", ["table_name"])
    op.create_index("ix_database_archive_runs_status", "database_archive_runs", ["status"])

    # 2) 高频查询复合索引（每个索引对应一次审计出的查询模式）
    op.create_index("ix_token_usage_user_id_created_at", "token_usage", ["user_id", "created_at"])
    op.create_index("ix_token_usage_created_at", "token_usage", ["created_at"])  # 归档扫描
    op.create_index("ix_operation_logs_user_id_created_at", "operation_logs", ["user_id", "created_at"])
    op.create_index("ix_operation_logs_created_at", "operation_logs", ["created_at"])  # 时间扫描/归档
    op.create_index("ix_llm_call_logs_module_name_created_at", "llm_call_logs", ["module_name", "created_at"])  # 成本统计
    op.create_index("ix_llm_call_logs_status_created_at", "llm_call_logs", ["status", "created_at"])  # 失败扫描
    op.create_index("ix_login_logs_user_id_created_at", "login_logs", ["user_id", "created_at"])
    op.create_index("ix_login_logs_event_type_created_at", "login_logs", ["event_type", "created_at"])
    op.create_index("ix_admin_audit_logs_operator_id_created_at", "admin_audit_logs", ["operator_id", "created_at"])
    op.create_index(
        "ix_legal_notification_events_user_channel_status_created_at",
        "legal_notification_events", ["user_id", "channel", "status", "created_at"],
    )  # 通知列表/未读
    op.create_index("ix_legal_deadlines_org_deadline_at", "legal_deadlines", ["organization_id", "deadline_at"])
    op.create_index("ix_connector_sync_jobs_connector_id_status", "connector_sync_jobs", ["connector_id", "status"])
    op.create_index("ix_webhook_deliveries_status_created_at", "webhook_deliveries", ["status", "created_at"])
    op.create_index("ix_legal_async_jobs_org_status", "legal_async_jobs", ["organization_id", "status"])
    op.create_index("ix_workflow_executions_status_created_at", "workflow_executions", ["status", "created_at"])
    op.create_index("ix_document_parse_jobs_status_created_at", "document_parse_jobs", ["status", "created_at"])
    op.create_index("ix_scheduled_workflows_enabled_next_run_at", "scheduled_workflows", ["enabled", "next_run_at"])

    # 3) 业务唯一约束（先清脏数据再建约束）
    _dedupe_keep_latest("legal_notification_preferences", ["user_id", "event_type"])
    with op.batch_alter_table("legal_notification_preferences") as batch_op:
        batch_op.create_unique_constraint(
            "uq_legal_notification_preferences_user_event", ["user_id", "event_type"],
        )
    _dedupe_keep_latest("webhook_subscriptions", ["app_id", "event_type"])
    with op.batch_alter_table("webhook_subscriptions") as batch_op:
        batch_op.create_unique_constraint(
            "uq_webhook_subscriptions_app_event", ["app_id", "event_type"],
        )

    # 4) 子表外键 ON DELETE CASCADE（与 ORM cascade 对齐，MySQL 生效）
    _replace_fk_with_cascade("document_chunks", "document_id", "documents")
    _replace_fk_with_cascade("document_parse_jobs", "document_id", "documents")
    _replace_fk_with_cascade("document_qa_records", "document_id", "documents")
    _replace_fk_with_cascade("task_logs", "task_id", "tasks")
    _replace_fk_with_cascade("task_comments", "task_id", "tasks")


def downgrade() -> None:
    # 撤销 MySQL 下的级联外键（SQLite 从未添加，跳过）
    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        for table, column in (
            ("document_chunks", "document_id"),
            ("document_parse_jobs", "document_id"),
            ("document_qa_records", "document_id"),
            ("task_logs", "task_id"),
            ("task_comments", "task_id"),
        ):
            with op.batch_alter_table(table) as batch_op:
                batch_op.drop_constraint(f"fk_{table}_{column}_cascade", type_="foreignkey")

    for index in (
        "ix_scheduled_workflows_enabled_next_run_at",
        "ix_document_parse_jobs_status_created_at",
        "ix_workflow_executions_status_created_at",
        "ix_legal_async_jobs_org_status",
        "ix_webhook_deliveries_status_created_at",
        "ix_connector_sync_jobs_connector_id_status",
        "ix_legal_deadlines_org_deadline_at",
        "ix_legal_notification_events_user_channel_status_created_at",
        "ix_admin_audit_logs_operator_id_created_at",
        "ix_login_logs_event_type_created_at",
        "ix_login_logs_user_id_created_at",
        "ix_llm_call_logs_status_created_at",
        "ix_llm_call_logs_module_name_created_at",
        "ix_operation_logs_created_at",
        "ix_operation_logs_user_id_created_at",
        "ix_token_usage_created_at",
        "ix_token_usage_user_id_created_at",
    ):
        op.drop_index(index)

    with op.batch_alter_table("webhook_subscriptions") as batch_op:
        batch_op.drop_constraint("uq_webhook_subscriptions_app_event", type_="unique")
    with op.batch_alter_table("legal_notification_preferences") as batch_op:
        batch_op.drop_constraint("uq_legal_notification_preferences_user_event", type_="unique")

    op.drop_index("ix_database_archive_runs_status", table_name="database_archive_runs")
    op.drop_index("ix_database_archive_runs_table_name", table_name="database_archive_runs")
    op.drop_table("database_archive_runs")
