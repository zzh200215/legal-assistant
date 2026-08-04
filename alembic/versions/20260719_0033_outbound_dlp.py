"""add outbound dlp controls

Revision ID: 20260719_0033
Revises: 20260719_0032
Create Date: 2026-07-19 14:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260719_0033"
down_revision = "20260719_0032"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("outbound_email_policies", sa.Column("dlp_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("outbound_email_policies", sa.Column("dlp_action", sa.String(length=16), nullable=False, server_default="block"))
    op.add_column("email_send_requests", sa.Column("dlp_status", sa.String(length=32), nullable=False, server_default="not_scanned"))
    op.add_column("email_send_requests", sa.Column("dlp_findings_json", sa.Text(), nullable=True))
    op.add_column("email_send_requests", sa.Column("dlp_scanned_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_email_send_requests_dlp_status", "email_send_requests", ["dlp_status"])


def downgrade():
    op.drop_index("ix_email_send_requests_dlp_status", table_name="email_send_requests")
    op.drop_column("email_send_requests", "dlp_scanned_at")
    op.drop_column("email_send_requests", "dlp_findings_json")
    op.drop_column("email_send_requests", "dlp_status")
    op.drop_column("outbound_email_policies", "dlp_action")
    op.drop_column("outbound_email_policies", "dlp_enabled")
