"""persist document chunk visual metadata

Revision ID: 20260626_0009
Revises: 20260626_0008
Create Date: 2026-06-26 14:05:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260626_0009"
down_revision: Union[str, None] = "20260626_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("document_chunks", sa.Column("visual_tags", sa.Text(), nullable=True))
    op.add_column("document_chunks", sa.Column("ocr_quality", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("document_chunks", "ocr_quality")
    op.drop_column("document_chunks", "visual_tags")
