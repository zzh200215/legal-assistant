"""咨询导出记录（step63）。

Revision ID: 20261028_0113
Revises: 20261027_0112

保存导出时的 Markdown 快照，支持回看与重新下载；content 只存文本，
不接对象存储，避免为小文件引入额外依赖。
"""

import sqlalchemy as sa

from alembic import op

revision = "20261028_0113"
down_revision = "20261027_0112"
branch_labels = None
depends_on = None

TABLE = "legal_consultation_exports"


def _has_table() -> bool:
    return sa.inspect(op.get_bind()).has_table(TABLE)


def upgrade() -> None:
    if _has_table():
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "consultation_id", sa.Integer(),
            sa.ForeignKey("legal_consultations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("format", sa.String(16), nullable=False, server_default="markdown"),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_version", sa.String(32), nullable=True),
        sa.Column("status_at_export", sa.String(32), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_legal_consultation_exports_consultation_id", TABLE,
                    ["consultation_id"])
    op.create_index("ix_legal_consultation_exports_organization_id", TABLE,
                    ["organization_id"])
    op.create_index("ix_legal_consultation_exports_user_id", TABLE, ["user_id"])
    op.create_index("ix_legal_consultation_exports_created_at", TABLE, ["created_at"])


def downgrade() -> None:
    if not _has_table():
        return
    op.drop_index("ix_legal_consultation_exports_created_at", table_name=TABLE)
    op.drop_index("ix_legal_consultation_exports_user_id", table_name=TABLE)
    op.drop_index("ix_legal_consultation_exports_organization_id", table_name=TABLE)
    op.drop_index("ix_legal_consultation_exports_consultation_id", table_name=TABLE)
    op.drop_table(TABLE)
