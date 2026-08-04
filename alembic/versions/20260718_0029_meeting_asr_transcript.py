"""add meeting transcription provenance

Revision ID: 20260718_0029
Revises: 20260718_0028
"""
from alembic import op
import sqlalchemy as sa


revision = "20260718_0029"
down_revision = "20260718_0028"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("meetings", sa.Column("transcript_segments", sa.Text(), nullable=True))
    op.add_column("meetings", sa.Column("transcript_source", sa.String(length=32), nullable=True))


def downgrade():
    op.drop_column("meetings", "transcript_source")
    op.drop_column("meetings", "transcript_segments")
