"""add imap mailbox connector storage

Revision ID: 20260714_0019
Revises: 20260712_0018
Create Date: 2026-07-14
"""

from alembic import op
import sqlalchemy as sa


revision = "20260714_0019"
down_revision = "20260712_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("external_connectors", sa.Column("credential_ciphertext", sa.Text(), nullable=True))
    op.add_column("external_connectors", sa.Column("sync_cursor_json", sa.Text(), nullable=True))
    op.create_table(
        "mailbox_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("connector_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("message_uid", sa.String(length=128), nullable=False),
        sa.Column("mailbox", sa.String(length=128), nullable=False, server_default="INBOX"),
        sa.Column("thread_id", sa.String(length=256), nullable=True),
        sa.Column("sender", sa.String(length=512), nullable=True),
        sa.Column("recipient", sa.String(length=512), nullable=True),
        sa.Column("subject", sa.String(length=512), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=32), nullable=False, server_default="other"),
        sa.Column("importance", sa.String(length=32), nullable=False, server_default="normal"),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["connector_id"], ["external_connectors.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("connector_id", "message_uid", name="uq_mailbox_messages_connector_uid"),
    )
    op.create_index("ix_mailbox_messages_connector_id", "mailbox_messages", ["connector_id"])
    op.create_index("ix_mailbox_messages_user_id", "mailbox_messages", ["user_id"])
    op.create_index("ix_mailbox_messages_category", "mailbox_messages", ["category"])
    op.create_index("ix_mailbox_messages_importance", "mailbox_messages", ["importance"])
    op.create_index("ix_mailbox_messages_received_at", "mailbox_messages", ["received_at"])
    op.create_index("ix_mailbox_messages_task_id", "mailbox_messages", ["task_id"])


def downgrade() -> None:
    op.drop_table("mailbox_messages")
    op.drop_column("external_connectors", "sync_cursor_json")
    op.drop_column("external_connectors", "credential_ciphertext")
