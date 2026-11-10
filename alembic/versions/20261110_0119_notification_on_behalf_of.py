"""通知事件记录代理来源：legal_notification_events 增加可空 on_behalf_of_user_id。

代理接收人生效后，事件的 user_id 是代理，原收件人记录在 on_behalf_of_user_id：
- 去重键纳入原收件人，避免多个原收件人的同一类事件在代理处互相覆盖；
- 任务提醒按「任务 + 原收件人 + 模板」去重，扫描时不会重复创建代理事件；
- API 读接口下发该字段，正文前缀显示「代 X 接收」，审计可追溯。

Revision ID: 20261110_0119
Revises: 20261102_0118
"""

from alembic import op
import sqlalchemy as sa

revision = "20261110_0119"
down_revision = "20261102_0118"
branch_labels = None
depends_on = None

TABLE = "legal_notification_events"
COLUMN = "on_behalf_of_user_id"
INDEX = "ix_legal_notification_events_on_behalf_of_user_id"
FK = "fk_legal_notification_events_on_behalf_of_user_id"


def _table_exists(bind, name: str) -> bool:
    return name in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, TABLE) or not _table_exists(bind, "users"):
        return
    columns = {column["name"] for column in sa.inspect(bind).get_columns(TABLE)}
    if COLUMN in columns:
        return
    op.add_column(TABLE, sa.Column(COLUMN, sa.Integer(), nullable=True))
    op.create_index(INDEX, TABLE, [COLUMN])
    if bind.dialect.name != "sqlite":
        op.create_foreign_key(FK, TABLE, "users", [COLUMN], ["id"])


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, TABLE):
        return
    columns = {column["name"] for column in sa.inspect(bind).get_columns(TABLE)}
    if COLUMN not in columns:
        return
    if bind.dialect.name != "sqlite":
        op.drop_constraint(FK, TABLE, type_="foreignkey")
    op.drop_index(INDEX, table_name=TABLE)
    op.drop_column(TABLE, COLUMN)
