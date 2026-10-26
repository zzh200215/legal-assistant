"""通知模板增加 body_format（text / html / layout）。

- text：沿用历史行为，body_template 为纯文本；
- html：body_template 为整段 HTML（用于自定义版式），纯文本回退由服务端剥离标签生成；
- layout：body_template 为 JSON 覆写，只替换内置版式的文案字段，版式保持不变。

Revision ID: 20261027_0105
Revises: 20261026_0104
"""

import sqlalchemy as sa

from alembic import op

revision = "20261027_0105"
down_revision = "20261026_0104"
branch_labels = None
depends_on = None

TABLE = "notification_templates"


def _inspector():
    return sa.inspect(op.get_bind())


def _has_column(column: str) -> bool:
    return _inspector().has_table(TABLE) and column in {
        item["name"] for item in _inspector().get_columns(TABLE)
    }


def upgrade() -> None:
    if not _has_column("body_format"):
        with op.batch_alter_table(TABLE) as batch:
            batch.add_column(
                sa.Column("body_format", sa.String(16), nullable=False, server_default="text")
            )


def downgrade() -> None:
    if _has_column("body_format"):
        with op.batch_alter_table(TABLE) as batch:
            batch.drop_column("body_format")
