"""代理有效期：legal_notification_preferences 增加 delegate_start / delegate_end。

用本地 DATE 而不是 UTC 时间戳：用户想的是「请假到 11 月 20 日」，
按偏好时区的当地日期比较，结束日期当天仍然生效；NULL 表示不限制。

Revision ID: 20261111_0120
Revises: 20261110_0119
"""

from alembic import op
import sqlalchemy as sa

revision = "20261111_0120"
down_revision = "20261110_0119"
branch_labels = None
depends_on = None

TABLE = "legal_notification_preferences"
COLUMNS = (
    ("delegate_start", "代理生效开始日期（本地日期，含当天）"),
    ("delegate_end", "代理生效结束日期（本地日期，含当天）"),
)


def _table_exists(bind, name: str) -> bool:
    return name in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, TABLE):
        return
    existing = {column["name"] for column in sa.inspect(bind).get_columns(TABLE)}
    for name, comment in COLUMNS:
        if name in existing:
            continue
        op.add_column(TABLE, sa.Column(name, sa.Date(), nullable=True, comment=comment))


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, TABLE):
        return
    existing = {column["name"] for column in sa.inspect(bind).get_columns(TABLE)}
    for name, _comment in reversed(COLUMNS):
        if name in existing:
            op.drop_column(TABLE, name)
