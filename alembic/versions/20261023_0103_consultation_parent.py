"""追问咨询指向原咨询（parent_id），用于补充前后引用对比。

Revision ID: 20261023_0103
Revises: 20261022_0102
"""

import sqlalchemy as sa

from alembic import op

revision = "20261023_0103"
down_revision = "20261022_0102"
branch_labels = None
depends_on = None

TABLE = "legal_consultations"


def _inspector():
    return sa.inspect(op.get_bind())


def _has_column(column: str) -> bool:
    return _inspector().has_table(TABLE) and column in {
        item["name"] for item in _inspector().get_columns(TABLE)
    }


def upgrade() -> None:
    if not _has_column("parent_id"):
        with op.batch_alter_table(TABLE) as batch:
            batch.add_column(sa.Column("parent_id", sa.Integer(), nullable=True))
            batch.create_index("ix_legal_consultations_parent_id", ["parent_id"])


def downgrade() -> None:
    if _has_column("parent_id"):
        with op.batch_alter_table(TABLE) as batch:
            batch.drop_index("ix_legal_consultations_parent_id")
            batch.drop_column("parent_id")
