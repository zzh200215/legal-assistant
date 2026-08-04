"""prompt rollout and rollback

Revision ID: 20260623_0007
Revises: 20260623_0006
Create Date: 2026-06-23 18:30:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260623_0007"
down_revision = "20260623_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("prompt_templates") as batch_op:
        batch_op.add_column(sa.Column("previous_active_version_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("rollout_version_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("rollout_percentage", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("rollout_started_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_foreign_key(
            "fk_prompt_templates_previous_active_version_id",
            "prompt_template_versions",
            ["previous_active_version_id"],
            ["id"],
        )
        batch_op.create_foreign_key(
            "fk_prompt_templates_rollout_version_id",
            "prompt_template_versions",
            ["rollout_version_id"],
            ["id"],
        )

    op.execute("UPDATE prompt_templates SET rollout_percentage = 0 WHERE rollout_percentage IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("prompt_templates") as batch_op:
        batch_op.drop_constraint("fk_prompt_templates_rollout_version_id", type_="foreignkey")
        batch_op.drop_constraint("fk_prompt_templates_previous_active_version_id", type_="foreignkey")
        batch_op.drop_column("rollout_started_at")
        batch_op.drop_column("rollout_percentage")
        batch_op.drop_column("rollout_version_id")
        batch_op.drop_column("previous_active_version_id")
