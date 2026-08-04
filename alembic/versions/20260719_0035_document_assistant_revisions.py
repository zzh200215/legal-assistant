"""add document assistant revisions

Revision ID: 20260719_0035
Revises: 20260719_0034
Create Date: 2026-07-19 15:25:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260719_0035"
down_revision = "20260719_0034"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "document_assistant_revisions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("artifact_id", sa.Integer(), sa.ForeignKey("document_assistant_artifacts.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("turn_index", sa.Integer(), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=True),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_document_assistant_revisions_artifact_id", "document_assistant_revisions", ["artifact_id"])
    op.create_index("ix_document_assistant_revisions_user_id", "document_assistant_revisions", ["user_id"])


def downgrade():
    op.drop_index("ix_document_assistant_revisions_user_id", table_name="document_assistant_revisions")
    op.drop_index("ix_document_assistant_revisions_artifact_id", table_name="document_assistant_revisions")
    op.drop_table("document_assistant_revisions")
