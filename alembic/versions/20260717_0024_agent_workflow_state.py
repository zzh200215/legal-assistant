"""persist agent workflow state snapshots

Revision ID: 20260717_0024
Revises: 20260714_0023
Create Date: 2026-07-17
"""
from alembic import op
import sqlalchemy as sa


revision = "20260717_0024"
down_revision = "20260714_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("workflow_state", sa.Text(), nullable=True))
    op.add_column("agent_runs", sa.Column("workflow_state_updated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "workflow_state_updated_at")
    op.drop_column("agent_runs", "workflow_state")
