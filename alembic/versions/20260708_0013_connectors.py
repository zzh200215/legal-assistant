"""add external connectors

Revision ID: 20260708_0013
Revises: 20260708_0012
Create Date: 2026-07-08 16:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260708_0013"
down_revision: Union[str, None] = "20260708_0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "external_connectors",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("connector_type", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("config_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_external_connectors_user_id", "external_connectors", ["user_id"])
    op.create_index("ix_external_connectors_organization_id", "external_connectors", ["organization_id"])
    op.create_index("ix_external_connectors_connector_type", "external_connectors", ["connector_type"])

    op.create_table(
        "connector_sync_jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("connector_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("sync_mode", sa.String(length=32), nullable=False, server_default="manual"),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["connector_id"], ["external_connectors.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_connector_sync_jobs_connector_id", "connector_sync_jobs", ["connector_id"])
    op.create_index("ix_connector_sync_jobs_user_id", "connector_sync_jobs", ["user_id"])
    op.create_index("ix_connector_sync_jobs_status", "connector_sync_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_connector_sync_jobs_status", table_name="connector_sync_jobs")
    op.drop_index("ix_connector_sync_jobs_user_id", table_name="connector_sync_jobs")
    op.drop_index("ix_connector_sync_jobs_connector_id", table_name="connector_sync_jobs")
    op.drop_table("connector_sync_jobs")
    op.drop_index("ix_external_connectors_connector_type", table_name="external_connectors")
    op.drop_index("ix_external_connectors_organization_id", table_name="external_connectors")
    op.drop_index("ix_external_connectors_user_id", table_name="external_connectors")
    op.drop_table("external_connectors")
