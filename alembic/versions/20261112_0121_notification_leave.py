"""全局请假代理：新增 legal_notification_leaves（每用户每组织一条）。

按事件类型配置代理要配多次、容易漏；请假是「整段时间不在」，用独立表存一条
用户级记录，解析收件人时优先于事件类型代理。有效期仍是本地 DATE（起止含当天），
NULL 表示不限；结束请假或到期清理时把代理/起止/备注一起清空。

Revision ID: 20261112_0121
Revises: 20261111_0120
"""

from alembic import op
import sqlalchemy as sa

revision = "20261112_0121"
down_revision = "20261111_0120"
branch_labels = None
depends_on = None

TABLE = "legal_notification_leaves"
UNIQUE = "uq_legal_notification_leaves_user_org"


def _table_exists(bind, name: str) -> bool:
    return name in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, TABLE):
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False,
                  comment="请假的用户（原收件人）"),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("delegate_user_id", sa.Integer(), nullable=True,
                  comment="请假期间的代理接收人"),
        sa.Column("leave_start", sa.Date(), nullable=True,
                  comment="请假开始日期（本地日期，含当天）"),
        sa.Column("leave_end", sa.Date(), nullable=True,
                  comment="请假结束日期（本地日期，含当天）"),
        sa.Column("note", sa.String(length=256), nullable=True,
                  comment="请假备注（如「出差」），仅展示"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["delegate_user_id"], ["users.id"]),
        sa.UniqueConstraint("user_id", "organization_id", name=UNIQUE),
    )
    op.create_index("ix_legal_notification_leaves_user_id", TABLE, ["user_id"])
    op.create_index("ix_legal_notification_leaves_organization_id", TABLE, ["organization_id"])


def downgrade() -> None:
    if _table_exists(op.get_bind(), TABLE):
        op.drop_table(TABLE)
