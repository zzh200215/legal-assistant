"""Agent 可观测性与审批生命周期加固。

- agent_runs 增加：trace_id / organization_id（run 级可观测与租户隔离）、
  run_deadline_at（run 级超时）、retry_of_run_id、compensation_status（补偿状态）。
- agent_approval_requests 增加：step_id（绑定审批步骤）、param_digest（审批参数摘要，
  参数变化必须重新审批）、decided_by（操作者）、expires_at / revoked_at / revoke_reason
  （过期与撤销）。
- 新表 agent_audit_events：结构化审计事件流（计划/权限/工具执行/审批/状态变更/
  重试/超时/取消/补偿/错误分类），可查询，不做事件溯源。

使用 batch_alter_table 跨方言（MySQL 原生 ALTER；SQLite copy-and-move）。

Revision ID: 20260813_0073
Revises: 20260812_0072
Create Date: 2026-08-13
"""
from alembic import op
import sqlalchemy as sa

revision = "20260813_0073"
down_revision = "20260812_0072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── agent_runs：可观测性 / 租户 / 超时 / 补偿 ──────────────────────────────
    with op.batch_alter_table("agent_runs") as batch:
        batch.add_column(sa.Column("trace_id", sa.String(64), nullable=True))
        batch.add_column(sa.Column("organization_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("run_deadline_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("retry_of_run_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("compensation_status", sa.String(32), nullable=True))
        batch.create_index("ix_agent_runs_trace_id", ["trace_id"])
        batch.create_index("ix_agent_runs_organization_id", ["organization_id"])

    # ── agent_approval_requests：审批生命周期加固 ──────────────────────────────
    with op.batch_alter_table("agent_approval_requests") as batch:
        batch.add_column(sa.Column("step_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("param_digest", sa.String(64), nullable=True))
        batch.add_column(sa.Column("decided_by", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("revoke_reason", sa.Text(), nullable=True))

    # ── 新表 agent_audit_events ────────────────────────────────────────────────
    op.create_table(
        "agent_audit_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("step", sa.Integer(), nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("tool_name", sa.String(128), nullable=True),
        sa.Column("tool_version", sa.String(32), nullable=True),
        sa.Column("decision_json", sa.Text(), nullable=True),
        sa.Column("summary_json", sa.Text(), nullable=True),
        sa.Column("error_category", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_agent_audit_events_run_id", "agent_audit_events", ["run_id"])
    op.create_index("ix_agent_audit_events_trace_id", "agent_audit_events", ["trace_id"])
    op.create_index("ix_agent_audit_events_event_type", "agent_audit_events", ["event_type"])


def downgrade() -> None:
    op.drop_index("ix_agent_audit_events_event_type", table_name="agent_audit_events")
    op.drop_index("ix_agent_audit_events_trace_id", table_name="agent_audit_events")
    op.drop_index("ix_agent_audit_events_run_id", table_name="agent_audit_events")
    op.drop_table("agent_audit_events")
    with op.batch_alter_table("agent_approval_requests") as batch:
        batch.drop_column("revoke_reason")
        batch.drop_column("revoked_at")
        batch.drop_column("expires_at")
        batch.drop_column("decided_by")
        batch.drop_column("param_digest")
        batch.drop_column("step_id")
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_index("ix_agent_runs_organization_id")
        batch.drop_index("ix_agent_runs_trace_id")
        batch.drop_column("compensation_status")
        batch.drop_column("retry_of_run_id")
        batch.drop_column("run_deadline_at")
        batch.drop_column("organization_id")
        batch.drop_column("trace_id")
