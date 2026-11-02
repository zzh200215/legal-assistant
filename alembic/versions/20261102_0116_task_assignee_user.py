"""任务负责人真实用户：tasks 增加 assignee_user_id（保留 assignee 文本快照）。

负责人必须是同组织用户；旧数据 assignee 文本继续可读，新指派写 user_id + 显示名快照。
不物理删除旧字段，避免历史任务/导出/通知文案断裂。

Revision ID: 20261102_0116
Revises: 20261101_0115
"""

from alembic import op
import sqlalchemy as sa

revision = "20261102_0116"
down_revision = "20261101_0115"
branch_labels = None
depends_on = None

TABLE = "tasks"
INDEX = "ix_tasks_assignee_user_id"
FK = "fk_tasks_assignee_user_id"


def _table_exists(bind, name: str) -> bool:
    return name in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, TABLE) or not _table_exists(bind, "users"):
        return
    columns = {column["name"] for column in sa.inspect(bind).get_columns(TABLE)}
    if "assignee_user_id" in columns:
        return
    op.add_column(TABLE, sa.Column("assignee_user_id", sa.Integer(), nullable=True))
    op.create_index(INDEX, TABLE, ["assignee_user_id"])
    if bind.dialect.name != "sqlite":
        op.create_foreign_key(FK, TABLE, "users", ["assignee_user_id"], ["id"])


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, TABLE):
        return
    columns = {column["name"] for column in sa.inspect(bind).get_columns(TABLE)}
    if "assignee_user_id" not in columns:
        return
    if bind.dialect.name != "sqlite":
        op.drop_constraint(FK, TABLE, type_="foreignkey")
    op.drop_index(INDEX, table_name=TABLE)
    op.drop_column(TABLE, "assignee_user_id")
