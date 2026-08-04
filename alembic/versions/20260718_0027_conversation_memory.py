"""add layered conversation memory

Revision ID: 20260718_0027
Revises: 20260718_0026
"""
from alembic import op
import sqlalchemy as sa


revision = "20260718_0027"
down_revision = "20260718_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_session_memories",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("chat_sessions.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("summarized_through_message_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("session_id", name="uq_chat_session_memories_session_id"),
    )
    op.create_index("ix_chat_session_memories_session_id", "chat_session_memories", ["session_id"])
    op.create_index("ix_chat_session_memories_user_id", "chat_session_memories", ["user_id"])
    op.create_table(
        "user_preference_memories",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False, server_default="general"),
        sa.Column("preference_key", sa.String(length=128), nullable=False),
        sa.Column("preference_value", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="explicit"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("user_id", "category", "preference_key", name="uq_user_preference_memory_key"),
    )
    op.create_index("ix_user_preference_memories_user_id", "user_preference_memories", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_preference_memories_user_id", table_name="user_preference_memories")
    op.drop_table("user_preference_memories")
    op.drop_index("ix_chat_session_memories_user_id", table_name="chat_session_memories")
    op.drop_index("ix_chat_session_memories_session_id", table_name="chat_session_memories")
    op.drop_table("chat_session_memories")
