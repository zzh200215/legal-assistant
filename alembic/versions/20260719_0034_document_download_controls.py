"""add document download controls

Revision ID: 20260719_0034
Revises: 20260719_0033
Create Date: 2026-07-19 14:40:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260719_0034"
down_revision = "20260719_0033"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("download_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("documents", sa.Column("watermark_required", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column("documents", "watermark_required")
    op.drop_column("documents", "download_enabled")
