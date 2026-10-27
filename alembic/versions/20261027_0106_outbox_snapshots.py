"""投递积压快照表 + 采样/保留配置。

Revision ID: 20261027_0106
Revises: 20261027_0105
"""

import sqlalchemy as sa

from alembic import op

revision = "20261027_0106"
down_revision = "20261027_0105"
branch_labels = None
depends_on = None

TABLE = "outbox_snapshots"


def _has_table() -> bool:
    return sa.inspect(op.get_bind()).has_table(TABLE)


def upgrade() -> None:
    if _has_table():
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("organization_id", sa.Integer(),
                  sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("notification_backlog", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notification_dead_letter", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notification_retry_pending", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notification_stale_claims", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("email_backlog", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("email_dead_letter", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("email_retry_pending", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("email_stale_claims", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_outbox_snapshots_captured_at", TABLE, ["captured_at"])
    op.create_index("ix_outbox_snapshots_organization_id", TABLE, ["organization_id"])
    op.create_index("ix_outbox_snapshots_org_captured", TABLE,
                    ["organization_id", "captured_at"])


def downgrade() -> None:
    if _has_table():
        op.drop_index("ix_outbox_snapshots_org_captured", table_name=TABLE)
        op.drop_index("ix_outbox_snapshots_organization_id", table_name=TABLE)
        op.drop_index("ix_outbox_snapshots_captured_at", table_name=TABLE)
        op.drop_table(TABLE)
