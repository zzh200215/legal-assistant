"""add llm routing observability fields

Revision ID: 20260730_0051
Revises: 20260728_0050
"""

from alembic import op
import sqlalchemy as sa


revision = "20260730_0051"
down_revision = "20260728_0050"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("llm_call_logs", sa.Column("request_id", sa.String(length=36), nullable=True))
    op.add_column("llm_call_logs", sa.Column("routing_role", sa.String(length=16), nullable=True))
    op.add_column("llm_call_logs", sa.Column("routing_stage", sa.String(length=16), nullable=True))
    op.create_index("ix_llm_call_logs_request_id", "llm_call_logs", ["request_id"])
    op.create_index("ix_llm_call_logs_routing_role", "llm_call_logs", ["routing_role"])
    op.create_index("ix_llm_call_logs_routing_stage", "llm_call_logs", ["routing_stage"])


def downgrade():
    op.drop_index("ix_llm_call_logs_routing_stage", table_name="llm_call_logs")
    op.drop_index("ix_llm_call_logs_routing_role", table_name="llm_call_logs")
    op.drop_index("ix_llm_call_logs_request_id", table_name="llm_call_logs")
    op.drop_column("llm_call_logs", "routing_stage")
    op.drop_column("llm_call_logs", "routing_role")
    op.drop_column("llm_call_logs", "request_id")
