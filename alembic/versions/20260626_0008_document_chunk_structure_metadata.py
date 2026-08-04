"""persist document chunk structure metadata

Revision ID: 20260626_0008
Revises: 20260623_0007
Create Date: 2026-06-26 11:10:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260626_0008"
down_revision: Union[str, None] = "20260623_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("document_chunks", sa.Column("section_path", sa.Text(), nullable=True))
    op.add_column("document_chunks", sa.Column("segment_type", sa.String(length=64), nullable=True))
    op.add_column(
        "document_chunks",
        sa.Column("table_like", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("document_chunks", "table_like")
    op.drop_column("document_chunks", "segment_type")
    op.drop_column("document_chunks", "section_path")
