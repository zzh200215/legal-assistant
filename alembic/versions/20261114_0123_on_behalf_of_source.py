"""通知代收来源：legal_notification_events 增加可空 on_behalf_of_source。

通知改发给代理时有两条路径：全局请假（覆盖全部事件类型）与按事件类型设置的代理。
此前只记录「原收件人」，通知中心无法区分是哪种代收。新增来源列后：
- 创建时按实际命中路径写入 leave / event，本人接收写 NULL；
- 读接口可按来源筛选，通知中心给出「请假代收 / 类型代收」标签；
- 历史上的代收事件保持 NULL（当时没有记录路径，不猜测回填），列表按「代收」展示。

Revision ID: 20261114_0123
Revises: 20261112_0122
"""

from alembic import op
import sqlalchemy as sa

revision = "20261114_0123"
down_revision = "20261112_0122"
branch_labels = None
depends_on = None

TABLE = "legal_notification_events"
COLUMN = "on_behalf_of_source"


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
        sa.Column(COLUMN, sa.String(length=16), nullable=True,
                  comment="代收来源：leave=全局请假代收，event=按事件类型代收；NULL=本人接收或历史数据"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, TABLE):
        return
    columns = {column["name"] for column in sa.inspect(bind).get_columns(TABLE)}
    if COLUMN not in columns:
        return
    op.drop_column(TABLE, COLUMN)
