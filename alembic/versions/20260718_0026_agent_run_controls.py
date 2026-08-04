"""add agent run controls

Revision ID: 20260718_0026
Revises: 20260718_0025
"""
from alembic import op
import sqlalchemy as sa

revision = "20260718_0026"
down_revision = "20260718_0025"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("agent_runs", sa.Column("cancel_reason", sa.Text(), nullable=True))

def downgrade() -> None:
    op.drop_column("agent_runs", "cancel_reason")
    op.drop_column("agent_runs", "cancel_requested_at")
