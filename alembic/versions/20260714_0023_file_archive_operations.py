"""add file archive operations

Revision ID: 20260714_0023
Revises: 20260714_0022
Create Date: 2026-07-14
"""
from alembic import op
import sqlalchemy as sa

revision = "20260714_0023"
down_revision = "20260714_0022"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table("file_archive_operations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False), sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False), sa.Column("destination_path", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="previewed"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True), sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True), sa.Column("reverted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_file_archive_operations_user_id", "file_archive_operations", ["user_id"])
    op.create_index("ix_file_archive_operations_status", "file_archive_operations", ["status"])

def downgrade() -> None: op.drop_table("file_archive_operations")
