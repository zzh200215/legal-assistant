"""任务协作者真实用户：task_collaborators 关联表（兼容旧 collaborators 文本快照）。

新数据写 user_id 关联 + 显示名快照；旧任务的自由文本协作者继续可读，
owner 重新选择后替换为真实用户。协作者可更新状态/进度与评论。

Revision ID: 20261102_0117
Revises: 20261102_0116
"""

from alembic import op
import sqlalchemy as sa

revision = "20261102_0117"
down_revision = "20261102_0116"
branch_labels = None
depends_on = None

TABLE = "task_collaborators"


def _table_exists(bind, name: str) -> bool:
    return name in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "tasks") or not _table_exists(bind, "users"):
        return
    if _table_exists(bind, TABLE):
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("task_id", "user_id", name="uq_task_collaborators_task_user"),
    )
    op.create_index("ix_task_collaborators_task_id", TABLE, ["task_id"])
    op.create_index("ix_task_collaborators_user_id", TABLE, ["user_id"])


def downgrade() -> None:
    if _table_exists(op.get_bind(), TABLE):
        op.drop_table(TABLE)
