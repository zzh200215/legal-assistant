"""任务归档：tasks 增加 archived_at（软删除/隐藏）。

归档任务默认不出现在任务列表、统计与到期提醒中，但仍可通过详情读取并恢复；
不物理删除，保留评论、日志与来源关联。

Revision ID: 20261101_0115
Revises: 20261030_0114
"""

from alembic import op
import sqlalchemy as sa

revision = "20261101_0115"
down_revision = "20261030_0114"
branch_labels = None
depends_on = None

TABLE = "tasks"
INDEX = "ix_tasks_archived_at"


def _table_exists(bind, name: str) -> bool:
    return name in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, TABLE):
        return
    columns = {column["name"] for column in sa.inspect(bind).get_columns(TABLE)}
    if "archived_at" in columns:
        return
    op.add_column(TABLE, sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(INDEX, TABLE, ["archived_at"])


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, TABLE):
        return
    columns = {column["name"] for column in sa.inspect(bind).get_columns(TABLE)}
    if "archived_at" not in columns:
        return
    op.drop_index(INDEX, table_name=TABLE)
    op.drop_column(TABLE, "archived_at")
