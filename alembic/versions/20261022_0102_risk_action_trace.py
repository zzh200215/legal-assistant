"""风险项处置写入审核动作表（逐条追溯）。

accept/mitigate/dismiss 之前只改 reports 的 risks_json，没有独立记录；本迁移给
legal_review_actions 加 risk_index / risk_title，使「谁在什么时候忽略了哪条风险」可查。

Revision ID: 20261022_0102
Revises: 20261022_0101
"""

import sqlalchemy as sa

from alembic import op

revision = "20261022_0102"
down_revision = "20261022_0101"
branch_labels = None
depends_on = None

TABLE = "legal_review_actions"


def _inspector():
    return sa.inspect(op.get_bind())


def _has_column(column: str) -> bool:
    return _inspector().has_table(TABLE) and column in {
        item["name"] for item in _inspector().get_columns(TABLE)
    }


def upgrade() -> None:
    if not _has_column("risk_index"):
        with op.batch_alter_table(TABLE) as batch:
            batch.add_column(sa.Column("risk_index", sa.Integer(), nullable=True))
    if not _has_column("risk_title"):
        with op.batch_alter_table(TABLE) as batch:
            batch.add_column(sa.Column("risk_title", sa.String(length=256), nullable=True))
    if _has_column("risk_index"):
        with op.batch_alter_table(TABLE) as batch:
            batch.create_index("ix_legal_review_actions_risk_index", ["risk_index"])


def downgrade() -> None:
    if _has_column("risk_index"):
        with op.batch_alter_table(TABLE) as batch:
            batch.drop_index("ix_legal_review_actions_risk_index")
            batch.drop_column("risk_index")
    if _has_column("risk_title"):
        with op.batch_alter_table(TABLE) as batch:
            batch.drop_column("risk_title")
