"""add rag chunk metadata and llm call logs

Revision ID: 20260621_0005
Revises: 20260621_0004
Create Date: 2026-06-21 10:20:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260621_0005"
down_revision: Union[str, None] = "20260621_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("document_chunks", sa.Column("page_number", sa.Integer(), nullable=True))
    op.add_column("document_chunks", sa.Column("section_title", sa.String(length=256), nullable=True))

    op.create_table(
        "llm_call_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("module_name", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("prompt_template", sa.String(length=128), nullable=True),
        sa.Column("prompt_version", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="success"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("request_excerpt", sa.Text(), nullable=True),
        sa.Column("response_excerpt", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_llm_call_logs_id"), "llm_call_logs", ["id"], unique=False)
    op.create_index(op.f("ix_llm_call_logs_user_id"), "llm_call_logs", ["user_id"], unique=False)
    op.create_index(op.f("ix_llm_call_logs_module_name"), "llm_call_logs", ["module_name"], unique=False)
    op.create_index(op.f("ix_llm_call_logs_action"), "llm_call_logs", ["action"], unique=False)
    op.create_index(op.f("ix_llm_call_logs_prompt_template"), "llm_call_logs", ["prompt_template"], unique=False)
    op.create_index(op.f("ix_llm_call_logs_status"), "llm_call_logs", ["status"], unique=False)
    op.create_index(op.f("ix_llm_call_logs_created_at"), "llm_call_logs", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_llm_call_logs_created_at"), table_name="llm_call_logs")
    op.drop_index(op.f("ix_llm_call_logs_status"), table_name="llm_call_logs")
    op.drop_index(op.f("ix_llm_call_logs_prompt_template"), table_name="llm_call_logs")
    op.drop_index(op.f("ix_llm_call_logs_action"), table_name="llm_call_logs")
    op.drop_index(op.f("ix_llm_call_logs_module_name"), table_name="llm_call_logs")
    op.drop_index(op.f("ix_llm_call_logs_user_id"), table_name="llm_call_logs")
    op.drop_index(op.f("ix_llm_call_logs_id"), table_name="llm_call_logs")
    op.drop_table("llm_call_logs")

    op.drop_column("document_chunks", "section_title")
    op.drop_column("document_chunks", "page_number")
