"""add connector sync job details

Revision ID: 20260708_0014
Revises: 20260708_0013
Create Date: 2026-07-08 23:40:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260708_0014"
down_revision = "20260708_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("connector_sync_jobs", sa.Column("result_detail_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("connector_sync_jobs", "result_detail_json")
