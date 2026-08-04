"""add email draft metadata

Revision ID: 20260708_0017
Revises: 20260708_0016
Create Date: 2026-07-08 23:45:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260708_0017"
down_revision: Union[str, None] = "20260708_0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("email_drafts", sa.Column("metadata_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("email_drafts", "metadata_json")
