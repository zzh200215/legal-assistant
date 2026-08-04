"""add scheduled workflows

Revision ID: 20260714_0020
Revises: 20260714_0019
Create Date: 2026-07-14
"""

from alembic import op
import sqlalchemy as sa


revision = "20260714_0020"
down_revision = "20260714_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scheduled_workflows",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("department_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("workflow_type", sa.String(length=64), nullable=False),
        sa.Column("frequency", sa.String(length=32), nullable=False, server_default="daily"),
        sa.Column("run_time", sa.String(length=5), nullable=False, server_default="09:00"),
        sa.Column("weekday", sa.Integer(), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Asia/Shanghai"),
        sa.Column("config_json", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(length=32), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scheduled_workflows_user_id", "scheduled_workflows", ["user_id"])
    op.create_index("ix_scheduled_workflows_organization_id", "scheduled_workflows", ["organization_id"])
    op.create_index("ix_scheduled_workflows_department_id", "scheduled_workflows", ["department_id"])
    op.create_index("ix_scheduled_workflows_workflow_type", "scheduled_workflows", ["workflow_type"])
    op.create_index("ix_scheduled_workflows_enabled", "scheduled_workflows", ["enabled"])
    op.create_index("ix_scheduled_workflows_next_run_at", "scheduled_workflows", ["next_run_at"])
    op.create_table(
        "workflow_executions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("schedule_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("trigger_type", sa.String(length=32), nullable=False, server_default="scheduled"),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("celery_task_id", sa.String(length=128), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("result_detail_json", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["schedule_id"], ["scheduled_workflows.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_workflow_executions_idempotency_key"),
    )
    for column in ("schedule_id", "user_id", "status", "celery_task_id"):
        op.create_index(f"ix_workflow_executions_{column}", "workflow_executions", [column])


def downgrade() -> None:
    op.drop_table("workflow_executions")
    op.drop_table("scheduled_workflows")
