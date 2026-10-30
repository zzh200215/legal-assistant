"""任务补 case_id（案件任务视图）。

Task 原只有 user/organization/department，没有案件维度；咨询生成的跟进任务
只能靠 description/source 关联案件。本迁移补可空 case_id + 索引 + FK。

Revision ID: 20261030_0114
Revises: 20261028_0113
"""

from alembic import op
import sqlalchemy as sa

revision = "20261030_0114"
down_revision = "20261028_0113"
branch_labels = None
depends_on = None

TABLE = "tasks"
INDEX = "ix_tasks_case_id"
FK = "fk_tasks_case_id"


def _table_exists(bind, name: str) -> bool:
    return name in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, TABLE) or not _table_exists(bind, "legal_cases"):
        return
    columns = {column["name"] for column in sa.inspect(bind).get_columns(TABLE)}
    if "case_id" in columns:
        return
    op.add_column(TABLE, sa.Column("case_id", sa.Integer(), nullable=True))
    op.create_index(INDEX, TABLE, ["case_id"])
    if bind.dialect.name != "sqlite":
        op.create_foreign_key(FK, TABLE, "legal_cases", ["case_id"], ["id"])


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, TABLE):
        return
    columns = {column["name"] for column in sa.inspect(bind).get_columns(TABLE)}
    if "case_id" not in columns:
        return
    if bind.dialect.name != "sqlite":
        op.drop_constraint(FK, TABLE, type_="foreignkey")
    op.drop_index(INDEX, table_name=TABLE)
    op.drop_column(TABLE, "case_id")
