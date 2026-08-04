"""Expand short sensitive columns before AES-256-GCM transparent encryption.

Revision ID: 20260725_0048
Revises: 20260725_0047
"""

from alembic import op
import sqlalchemy as sa

revision = "20260725_0048"
down_revision = "20260725_0047"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("legal_cases", "client_name", existing_type=sa.String(128), type_=sa.Text(), existing_nullable=True)
    op.alter_column("legal_cases", "opposing_party", existing_type=sa.String(256), type_=sa.Text(), existing_nullable=True)
    op.alter_column("legal_invoices", "client_contact", existing_type=sa.String(256), type_=sa.Text(), existing_nullable=True)


def downgrade():
    op.alter_column("legal_invoices", "client_contact", existing_type=sa.Text(), type_=sa.String(256), existing_nullable=True)
    op.alter_column("legal_cases", "opposing_party", existing_type=sa.Text(), type_=sa.String(256), existing_nullable=True)
    op.alter_column("legal_cases", "client_name", existing_type=sa.Text(), type_=sa.String(128), existing_nullable=True)
