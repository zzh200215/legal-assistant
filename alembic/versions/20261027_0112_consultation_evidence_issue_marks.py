"""引用质量问题处置标记（step52）。

Revision ID: 20261027_0112
Revises: 20261027_0111

支撑「标记已处理 / 重新打开」：一条咨询对应一行，不覆盖咨询正文；
`resolved_by` / `resolved_at` / `note` 只记录处置动作。
"""

import sqlalchemy as sa

from alembic import op

revision = "20261027_0112"
down_revision = "20261027_0111"
branch_labels = None
depends_on = None

TABLE = "consultation_evidence_issue_marks"


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
        sa.Column(
            "organization_id", sa.Integer(),
            sa.ForeignKey("organizations.id"), nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="resolved"),
        sa.Column("resolved_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_consultation_evidence_issue_marks_consultation_id", TABLE,
                    ["consultation_id"], unique=True)
    op.create_index("ix_consultation_evidence_issue_marks_organization_id", TABLE,
                    ["organization_id"])
    op.create_index("ix_consultation_evidence_issue_marks_status", TABLE, ["status"])


def downgrade() -> None:
    if not _has_table():
        return
    op.drop_index("ix_consultation_evidence_issue_marks_status", table_name=TABLE)
    op.drop_index("ix_consultation_evidence_issue_marks_organization_id", table_name=TABLE)
    op.drop_index("ix_consultation_evidence_issue_marks_consultation_id", table_name=TABLE)
    op.drop_table(TABLE)
