"""请假代理通知去重：legal_notification_leaves 增加 leave_period。

代收开始/交还通知按「请假轮次」去重：同一次请假里重复保存设置不会重复通知，
结束请假或换代理后轮次 +1，新一次请假会重新通知代理。默认 0 保持历史行为。

Revision ID: 20261112_0122
Revises: 20261112_0121
"""

from alembic import op
import sqlalchemy as sa

revision = "20261112_0122"
down_revision = "20261112_0121"
branch_labels = None
depends_on = None

TABLE = "legal_notification_leaves"
COLUMN = "leave_period"


def _table_exists(bind, name: str) -> bool:
    return name in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, TABLE):
        return
    columns = {column["name"] for column in sa.inspect(bind).get_columns(TABLE)}
    if COLUMN in columns:
        return
    op.add_column(
        TABLE,
        sa.Column(COLUMN, sa.Integer(), nullable=False, server_default="0",
                  comment="请假轮次：换代理时 +1，用于代收开始/交还通知去重"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, TABLE):
        return
    columns = {column["name"] for column in sa.inspect(bind).get_columns(TABLE)}
    if COLUMN not in columns:
        return
    op.drop_column(TABLE, COLUMN)
