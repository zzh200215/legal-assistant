"""Persist the immutable review-policy snapshot used by each review.

Revision ID: 20260728_0050
Revises: 20260728_0049
"""

from alembic import op
import sqlalchemy as sa


revision = "20260728_0050"
down_revision = "20260728_0049"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("legal_contract_reviews", sa.Column("review_policy_id", sa.Integer(), nullable=True))
    op.add_column("legal_contract_reviews", sa.Column("review_policy_version", sa.Integer(), nullable=True))
    op.add_column("legal_contract_reviews", sa.Column("review_policy_snapshot_json", sa.Text(), nullable=True))
    op.create_index("ix_legal_contract_reviews_review_policy_id", "legal_contract_reviews", ["review_policy_id"])


def downgrade():
    op.drop_index("ix_legal_contract_reviews_review_policy_id", table_name="legal_contract_reviews")
    op.drop_column("legal_contract_reviews", "review_policy_snapshot_json")
    op.drop_column("legal_contract_reviews", "review_policy_version")
    op.drop_column("legal_contract_reviews", "review_policy_id")
