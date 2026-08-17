"""Store recoverable webhook signing secrets and allow test deliveries without a subscription.

Revision ID: 20260814_0078
Revises: 20260813_0077
Create Date: 2026-08-14
"""

from alembic import op
import sqlalchemy as sa


revision = "20260814_0078"
down_revision = "20260813_0077"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("developer_apps") as batch_op:
        batch_op.add_column(sa.Column("webhook_secret_ciphertext", sa.Text(), nullable=True))
    with op.batch_alter_table("webhook_deliveries") as batch_op:
        batch_op.alter_column("subscription_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("webhook_deliveries") as batch_op:
        batch_op.alter_column("subscription_id", existing_type=sa.Integer(), nullable=False)
    with op.batch_alter_table("developer_apps") as batch_op:
        batch_op.drop_column("webhook_secret_ciphertext")
