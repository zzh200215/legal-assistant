"""Reviewer assignment and due dates for the legal review queue."""

import sqlalchemy as sa
from alembic import op

revision = "20261005_0090"
down_revision = "20261003_0089"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table in ("legal_consultations", "legal_contract_reviews", "legal_drafts"):
        columns = {column["name"] for column in inspector.get_columns(table)}
        indexes = {index.get("name") for index in inspector.get_indexes(table)}
        if "review_due_at" not in columns:
            op.add_column(table, sa.Column("review_due_at", sa.DateTime(timezone=True), nullable=True))
        index_name = f"ix_{table}_review_due_at"
        if index_name not in indexes:
            op.create_index(index_name, table, ["review_due_at"])


def downgrade() -> None:
    for table in ("legal_drafts", "legal_contract_reviews", "legal_consultations"):
        index_name = f"ix_{table}_review_due_at"
        op.drop_index(index_name, table_name=table)
        op.drop_column(table, "review_due_at")
