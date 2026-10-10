"""Add analytics_funnel_events for product funnel measurement (ux-audit 需补充信息)."""

import sqlalchemy as sa

from alembic import op

revision = "20261010_0096"
down_revision = "20261010_0095"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "analytics_funnel_events" not in inspector.get_table_names():
        op.create_table(
            "analytics_funnel_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("event", sa.String(64), nullable=False, index=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True, index=True),
            sa.Column("organization_id", sa.Integer(), nullable=True, index=True),
            sa.Column("case_id", sa.Integer(), nullable=True),
            sa.Column("path", sa.String(256), nullable=True),
            sa.Column("props_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "analytics_funnel_events" in inspector.get_table_names():
        op.drop_table("analytics_funnel_events")
