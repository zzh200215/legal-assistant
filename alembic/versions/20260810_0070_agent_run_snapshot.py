"""AgentRun 增加权限快照列：长流程 Agent 执行期间权限范围稳定。

Revision ID: 20260810_0070
Revises: 20260810_0069
Create Date: 2026-08-10
"""
from alembic import op
import sqlalchemy as sa

revision = "20260810_0070"
down_revision = "20260810_0069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("authorization_snapshot_id", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_agent_runs_authorization_snapshot_id", "agent_runs", ["authorization_snapshot_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_agent_runs_authorization_snapshot_id", table_name="agent_runs")
    op.drop_column("agent_runs", "authorization_snapshot_id")
