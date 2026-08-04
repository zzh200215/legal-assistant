"""#87/飞书 M1 前置: 建 feishu_bindings 表

企业自建应用用户绑定：open_id <-> user_id，回调验签复用 webhook HMAC 模式。
"""
from alembic import op
import sqlalchemy as sa

revision = "20260908_0060"
down_revision = "20260908_0059"
branch_labels = None
depends_on = None


def _table_exists(bind, name: str) -> bool:
    return name in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "feishu_bindings"):
        op.create_table(
            "feishu_bindings",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, unique=True, index=True),
            sa.Column("open_id", sa.String(128), nullable=False, unique=True, index=True, comment="飞书 open_id"),
            sa.Column("union_id", sa.String(128), nullable=True),
            sa.Column("app_id", sa.String(64), nullable=False, comment="企业自建应用 app_id"),
            sa.Column("status", sa.String(16), nullable=False, server_default="active", index=True,
                      comment="active / revoked"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "feishu_bindings"):
        op.drop_table("feishu_bindings")
