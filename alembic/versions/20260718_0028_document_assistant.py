"""add document assistant artifacts

Revision ID: 20260718_0028
Revises: 20260718_0027
"""
from alembic import op
import sqlalchemy as sa

revision = "20260718_0028"
down_revision = "20260718_0027"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("document_assistant_artifacts", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False), sa.Column("source_document_id", sa.Integer(), sa.ForeignKey("documents.id"), nullable=True), sa.Column("title", sa.String(length=256), nullable=False), sa.Column("artifact_type", sa.String(length=32), nullable=False), sa.Column("content", sa.Text(), nullable=False), sa.Column("language", sa.String(length=32), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")))
    op.create_index("ix_document_assistant_artifacts_user_id", "document_assistant_artifacts", ["user_id"])
    op.create_index("ix_document_assistant_artifacts_source_document_id", "document_assistant_artifacts", ["source_document_id"])
    op.create_index("ix_document_assistant_artifacts_artifact_type", "document_assistant_artifacts", ["artifact_type"])

def downgrade():
    op.drop_table("document_assistant_artifacts")
