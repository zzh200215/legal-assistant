"""add document qa records

Revision ID: 20260620_0003
Revises: 20260620_0002
Create Date: 2026-06-20 23:45:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260620_0003"
down_revision: Union[str, None] = "20260620_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_qa_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("citations", sa.Text(), nullable=True),
        sa.Column("hit_chunks", sa.Text(), nullable=True),
        sa.Column("model_name", sa.String(length=128), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_document_qa_records_id"), "document_qa_records", ["id"], unique=False)
    op.create_index(op.f("ix_document_qa_records_document_id"), "document_qa_records", ["document_id"], unique=False)
    op.create_index(op.f("ix_document_qa_records_user_id"), "document_qa_records", ["user_id"], unique=False)
    op.create_index(op.f("ix_document_qa_records_session_id"), "document_qa_records", ["session_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_document_qa_records_session_id"), table_name="document_qa_records")
    op.drop_index(op.f("ix_document_qa_records_user_id"), table_name="document_qa_records")
    op.drop_index(op.f("ix_document_qa_records_document_id"), table_name="document_qa_records")
    op.drop_index(op.f("ix_document_qa_records_id"), table_name="document_qa_records")
    op.drop_table("document_qa_records")
