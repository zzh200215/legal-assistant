"""#96/P3 客户门户反馈：legal_portal_feedback 表（👍/👎 + 备注）

Revision ID: 20260805_0063
Revises: 20260916_0062
Create Date: 2026-08-05
"""
from alembic import op
import sqlalchemy as sa

revision = "20260805_0063"
down_revision = "20260916_0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "legal_portal_feedback",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("portal_link_id", sa.Integer(), sa.ForeignKey("legal_portal_links.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False, index=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("legal_cases.id"), nullable=False, index=True),
        sa.Column("score", sa.Integer(), nullable=False, comment="1=有帮助 / -1=待改进"),
        sa.Column("note", sa.Text(), nullable=True, comment="待改进时的补充说明，≤500字"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
    )


def downgrade() -> None:
    op.drop_table("legal_portal_feedback")
