"""Merge alembic heads: 合并 08-05 回填的 portal_feedback 分支与 09-16 主链

历史原因：20260805_0063_portal_feedback 的 down_revision 被设为 20260916_0062，
导致迁移链出现两个 head，`alembic upgrade head` 无法解析，legal_portal_feedback
表从未被应用到任何库（联调 500：Table doesn't exist）。本迁移将两条分支合并。

Revision ID: 20260916_0064
Revises: 20260805_0063, 20260916_0063
Create Date: 2026-09-16
"""
from alembic import op
import sqlalchemy as sa

revision = "20260916_0064"
down_revision = ("20260805_0063", "20260916_0063")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 纯合并迁移，无 schema 变更；两条分支的内容在各自 upgrade 中已执行
    pass


def downgrade() -> None:
    pass
