"""邮件草稿支持 HTML 版正文（通知邮件 multipart/alternative）。

Revision ID: 20261026_0104
Revises: 20261023_0103
"""

import sqlalchemy as sa

from alembic import op

revision = "20261026_0104"
down_revision = "20261023_0103"
branch_labels = None
depends_on = None

TABLE = "email_drafts"


def _inspector():
    return sa.inspect(op.get_bind())


def _has_column(column: str) -> bool:
    return _inspector().has_table(TABLE) and column in {
        item["name"] for item in _inspector().get_columns(TABLE)
    }


def upgrade() -> None:
    if not _has_column("content_html"):
        with op.batch_alter_table(TABLE) as batch:
            batch.add_column(sa.Column("content_html", sa.Text(), nullable=True))


def downgrade() -> None:
    if _has_column("content_html"):
        with op.batch_alter_table(TABLE) as batch:
            batch.drop_column("content_html")
