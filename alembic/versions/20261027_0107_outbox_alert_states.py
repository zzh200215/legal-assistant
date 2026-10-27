"""投递告警持续状态表（每组织一行），支撑告警升级与恢复判定。

Revision ID: 20261027_0107
Revises: 20261027_0106
"""

import sqlalchemy as sa

from alembic import op

revision = "20261027_0107"
down_revision = "20261027_0106"
branch_labels = None
depends_on = None

TABLE = "outbox_alert_states"


def _has_table() -> bool:
    return sa.inspect(op.get_bind()).has_table(TABLE)


def upgrade() -> None:
    if _has_table():
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(),
                  sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("first_breached_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_breached_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("breach_streak", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_alert_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_outbox_alert_states_organization_id", TABLE, ["organization_id"],
                    unique=True)


def downgrade() -> None:
    if _has_table():
        op.drop_index("ix_outbox_alert_states_organization_id", table_name=TABLE)
        op.drop_table(TABLE)
