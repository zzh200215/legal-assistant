"""add calendar suggestions

Revision ID: 20260714_0022
Revises: 20260714_0021
Create Date: 2026-07-14
"""
from alembic import op
import sqlalchemy as sa

revision = "20260714_0022"
down_revision = "20260714_0021"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table("calendar_suggestions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False), sa.Column("user_id", sa.Integer(), nullable=False), sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=256), nullable=False), sa.Column("description", sa.Text(), nullable=True), sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False), sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attendees", sa.Text(), nullable=True), sa.Column("status", sa.String(length=32), nullable=False, server_default="proposed"), sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]), sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]), sa.PrimaryKeyConstraint("id"))
    for column in ("user_id", "task_id", "status"): op.create_index(f"ix_calendar_suggestions_{column}", "calendar_suggestions", [column])

def downgrade() -> None: op.drop_table("calendar_suggestions")
