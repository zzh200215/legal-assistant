"""Store encrypted Open API review inputs.

Revision ID: 20260728_0049
Revises: 20260725_0048
"""
from alembic import op
import sqlalchemy as sa

revision = "20260728_0049"
down_revision = "20260725_0048"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "legal_async_job_inputs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("legal_async_jobs.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("app_id", sa.Integer(), sa.ForeignKey("developer_apps.id"), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("content_ciphertext", sa.Text(), nullable=False),
        sa.Column("contract_type", sa.String(length=64)),
        sa.Column("review_policy_id", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_legal_async_job_inputs_job_id", "legal_async_job_inputs", ["job_id"])
    op.create_index("ix_legal_async_job_inputs_app_id", "legal_async_job_inputs", ["app_id"])
    op.create_index("ix_legal_async_job_inputs_request_fingerprint", "legal_async_job_inputs", ["request_fingerprint"])


def downgrade():
    op.drop_table("legal_async_job_inputs")
