"""P11-5 LegalCase 新增 is_strict_mode 字段

Revision ID: 20260725_0047
Revises: 20260725_0046
Create Date: 2026-07-25
"""

from alembic import op
import sqlalchemy as sa

revision = "20260725_0047"
down_revision = "20260725_0046"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "legal_cases",
        sa.Column("is_strict_mode", sa.Integer, nullable=False, server_default="0",
                  comment="1=严格模式：仅案件成员可访问；0=普通模式：组织成员均可访问"),
    )
    op.create_index("ix_legal_cases_is_strict_mode", "legal_cases", ["is_strict_mode"])


def downgrade():
    op.drop_index("ix_legal_cases_is_strict_mode", table_name="legal_cases")
    op.drop_column("legal_cases", "is_strict_mode")
