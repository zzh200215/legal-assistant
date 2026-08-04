"""create legal_cases table (missing from migration chain)

The migration chain referenced legal_cases (billing/deadline/contract FKs)
without ever creating it. Fresh MySQL databases failed at 0042. This
migration fills the gap and is intentionally placed before 0042 so later
revisions apply cleanly. Columns match the LegalCase model as it was before
0047 (is_strict_mode) and 0048 (Text widening) were added.

Revision ID: 20260725_0041b
Revises: 20260725_0041
"""

from alembic import op
import sqlalchemy as sa

revision = "20260725_0041b"
down_revision = "20260725_0041"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "legal_cases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("case_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("client_name", sa.String(128), nullable=True),
        sa.Column("opposing_party", sa.String(256), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_legal_cases_organization_id", "legal_cases", ["organization_id"])
    op.create_index("ix_legal_cases_user_id", "legal_cases", ["user_id"])
    op.create_index("ix_legal_cases_case_type", "legal_cases", ["case_type"])
    op.create_index("ix_legal_cases_status", "legal_cases", ["status"])


def downgrade():
    for index in [
        "ix_legal_cases_status",
        "ix_legal_cases_case_type",
        "ix_legal_cases_user_id",
        "ix_legal_cases_organization_id",
    ]:
        op.drop_index(index, table_name="legal_cases")
    op.drop_table("legal_cases")
