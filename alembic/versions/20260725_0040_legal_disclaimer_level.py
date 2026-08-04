"""legal_disclaimer_level

Revision ID: 20260725_0040
Revises: 20260725_0039
Create Date: 2026-07-25 00:40:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "20260725_0040"
down_revision = "20260725_0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "legal_consultations",
        sa.Column("disclaimer_level", sa.String(16), nullable=True, server_default="low", comment="免责声明级别: low/medium/high"),
    )


def downgrade() -> None:
    op.drop_column("legal_consultations", "disclaimer_level")
