"""add document conflict cases

Revision ID: 20260718_0025
Revises: 20260717_0024
Create Date: 2026-07-18
"""
from alembic import op
import sqlalchemy as sa


revision = "20260718_0025"
down_revision = "20260717_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_conflict_cases",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("department_id", sa.Integer(), sa.ForeignKey("departments.id"), nullable=True),
        sa.Column("document_ids_json", sa.Text(), nullable=False),
        sa.Column("conflict_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending_confirmation"),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id"), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_document_conflict_cases_user_id", "document_conflict_cases", ["user_id"])
    op.create_index("ix_document_conflict_cases_status", "document_conflict_cases", ["status"])
    op.create_index("ix_document_conflict_cases_task_id", "document_conflict_cases", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_document_conflict_cases_task_id", table_name="document_conflict_cases")
    op.drop_index("ix_document_conflict_cases_status", table_name="document_conflict_cases")
    op.drop_index("ix_document_conflict_cases_user_id", table_name="document_conflict_cases")
    op.drop_table("document_conflict_cases")
