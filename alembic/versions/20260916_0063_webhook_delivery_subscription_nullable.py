"""A10 修复 webhook 测试投递：subscription_id 改为可空（测试 ping 无订阅关联）

Revision ID: 20260916_0063
Revises: 20260916_0062
Create Date: 2026-09-16
"""
from alembic import op
import sqlalchemy as sa

revision = "20260916_0063"
down_revision = "20260916_0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("webhook_deliveries", "subscription_id",
                    existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.alter_column("webhook_deliveries", "subscription_id",
                    existing_type=sa.Integer(), nullable=False)
