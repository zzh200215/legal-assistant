"""add feedback_score and feedback_note to legal consultations, contract reviews, drafts

Revision ID: 20260725_0039
Revises: 20260724_0038
"""

from alembic import op
import sqlalchemy as sa

revision = "20260725_0039"
down_revision = "20260724_0038"
branch_labels = None
depends_on = None


def upgrade():
    # ── legal_consultations ──────────────────────────────────────
    op.add_column("legal_consultations", sa.Column("feedback_score", sa.Integer(), nullable=True))
    op.add_column("legal_consultations", sa.Column("feedback_note", sa.Text(), nullable=True))

    # ── legal_contract_reviews ───────────────────────────────────
    op.add_column("legal_contract_reviews", sa.Column("feedback_score", sa.Integer(), nullable=True))
    op.add_column("legal_contract_reviews", sa.Column("feedback_note", sa.Text(), nullable=True))

    # ── legal_drafts ─────────────────────────────────────────────
    op.add_column("legal_drafts", sa.Column("feedback_score", sa.Integer(), nullable=True))
    op.add_column("legal_drafts", sa.Column("feedback_note", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("legal_drafts", "feedback_note")
    op.drop_column("legal_drafts", "feedback_score")
    op.drop_column("legal_contract_reviews", "feedback_note")
    op.drop_column("legal_contract_reviews", "feedback_score")
    op.drop_column("legal_consultations", "feedback_note")
    op.drop_column("legal_consultations", "feedback_score")
