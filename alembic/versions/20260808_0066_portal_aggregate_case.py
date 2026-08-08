"""#79 P2 多链接聚合页：legal_portal_links 增加 aggregate_case 开关

Revision ID: 20260808_0066
Revises: 20260916_0065
Create Date: 2026-08-08
"""
from alembic import op
import sqlalchemy as sa

revision = "20260808_0066"
down_revision = "20260916_0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("legal_portal_links", sa.Column(
        "aggregate_case", sa.Integer(), nullable=False, server_default="0",
        comment="1=聚合该案件全部已发布客户可见内容（一个案件一个URL，#79 P2）"))


def downgrade() -> None:
    op.drop_column("legal_portal_links", "aggregate_case")
