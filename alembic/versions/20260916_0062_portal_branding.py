"""#95/P2-2 门户品牌化：organizations 增加 logo 与欢迎语配置

Revision ID: 20260916_0062
Revises: 20260915_0061
Create Date: 2026-09-16
"""
from alembic import op
import sqlalchemy as sa

revision = "20260916_0062"
down_revision = "20260915_0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("portal_logo_url", sa.String(length=512), nullable=True,
                                             comment="客户门户展示的律所 logo 图片 URL"))
    op.add_column("organizations", sa.Column("portal_welcome_message", sa.String(length=256), nullable=True,
                                             comment="客户门户顶部欢迎语"))


def downgrade() -> None:
    op.drop_column("organizations", "portal_welcome_message")
    op.drop_column("organizations", "portal_logo_url")
