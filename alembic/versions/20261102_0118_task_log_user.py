"""任务日志补操作人：task_logs 增加可空 user_id（历史日志保持 NULL）。

新日志写入触发人；评论本身已有 user_id。API 读时批量补 username/full_name，
前端显示「谁做了什么」。历史日志没有操作人时显示「系统/未知」。

Revision ID: 20261102_0118
Revises: 20261102_0117
"""

from alembic import op
import sqlalchemy as sa

revision = "20261102_0118"
down_revision = "20261102_0117"
branch_labels = None
depends_on = None

TABLE = "task_logs"
INDEX = "ix_task_logs_user_id"
FK = "fk_task_logs_user_id"


def _table_exists(bind, name: str) -> bool:
    return name in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, TABLE) or not _table_exists(bind, "users"):
        return
    columns = {column["name"] for column in sa.inspect(bind).get_columns(TABLE)}
    if "user_id" in columns:
        return
    op.add_column(TABLE, sa.Column("user_id", sa.Integer(), nullable=True))
    op.create_index(INDEX, TABLE, ["user_id"])
    if bind.dialect.name != "sqlite":
        op.create_foreign_key(FK, TABLE, "users", ["user_id"], ["id"])


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, TABLE):
        return
    columns = {column["name"] for column in sa.inspect(bind).get_columns(TABLE)}
    if "user_id" not in columns:
        return
    if bind.dialect.name != "sqlite":
        op.drop_constraint(FK, TABLE, type_="foreignkey")
    op.drop_index(INDEX, table_name=TABLE)
    op.drop_column(TABLE, "user_id")
