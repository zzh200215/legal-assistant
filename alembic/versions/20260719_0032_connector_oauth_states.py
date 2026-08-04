"""add connector oauth states

Revision ID: 20260719_0032
Revises: 20260718_0031
Create Date: 2026-07-19 10:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260719_0032"
down_revision = "20260718_0031"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "connector_oauth_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("connector_id", sa.Integer(), sa.ForeignKey("external_connectors.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("state_hash", sa.String(length=128), nullable=False, unique=True),
        sa.Column("code_verifier_ciphertext", sa.Text(), nullable=False),
        sa.Column("redirect_uri", sa.String(length=1024), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_connector_oauth_states_connector_id", "connector_oauth_states", ["connector_id"])
    op.create_index("ix_connector_oauth_states_user_id", "connector_oauth_states", ["user_id"])
    op.create_index("ix_connector_oauth_states_state_hash", "connector_oauth_states", ["state_hash"])
    op.create_index("ix_connector_oauth_states_expires_at", "connector_oauth_states", ["expires_at"])


def downgrade():
    op.drop_index("ix_connector_oauth_states_expires_at", table_name="connector_oauth_states")
    op.drop_index("ix_connector_oauth_states_state_hash", table_name="connector_oauth_states")
    op.drop_index("ix_connector_oauth_states_user_id", table_name="connector_oauth_states")
    op.drop_index("ix_connector_oauth_states_connector_id", table_name="connector_oauth_states")
    op.drop_table("connector_oauth_states")
