"""add document parse jobs

Revision ID: 20260620_0002
Revises: 20260620_0001
Create Date: 2026-06-20 23:10:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260620_0002"
down_revision: Union[str, None] = "20260620_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_parse_jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=True),
        sa.Column("current_step", sa.String(length=128), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_document_parse_jobs_id"), "document_parse_jobs", ["id"], unique=False)
    op.create_index(op.f("ix_document_parse_jobs_document_id"), "document_parse_jobs", ["document_id"], unique=False)
    op.create_index(op.f("ix_document_parse_jobs_user_id"), "document_parse_jobs", ["user_id"], unique=False)
    op.create_index(op.f("ix_document_parse_jobs_job_type"), "document_parse_jobs", ["job_type"], unique=False)
    op.create_index(op.f("ix_document_parse_jobs_task_id"), "document_parse_jobs", ["task_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_document_parse_jobs_task_id"), table_name="document_parse_jobs")
    op.drop_index(op.f("ix_document_parse_jobs_job_type"), table_name="document_parse_jobs")
    op.drop_index(op.f("ix_document_parse_jobs_user_id"), table_name="document_parse_jobs")
    op.drop_index(op.f("ix_document_parse_jobs_document_id"), table_name="document_parse_jobs")
    op.drop_index(op.f("ix_document_parse_jobs_id"), table_name="document_parse_jobs")
    op.drop_table("document_parse_jobs")
