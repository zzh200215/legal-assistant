"""add legal document version snapshots

Revision ID: 20260724_0037
Revises: 20260722_0036
"""

from alembic import op
import sqlalchemy as sa

revision = "20260724_0037"
down_revision = "20260722_0036"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "legal_document_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(256)),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status_at_snapshot", sa.String(32), nullable=False),
        sa.Column("snapshot_reason", sa.String(32), nullable=False, server_default="resubmit"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_legal_document_versions_target_type", "legal_document_versions", ["target_type"])
    op.create_index("ix_legal_document_versions_target_id", "legal_document_versions", ["target_id"])


def downgrade():
    op.drop_table("legal_document_versions")
