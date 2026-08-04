"""Phase 11 关键日期 / 门户 / 案件成员 / 进度更新

Revision ID: 20260725_0043
Revises: 20260725_0042
Create Date: 2026-07-25
"""

from alembic import op
import sqlalchemy as sa

revision = "20260725_0043"
down_revision = "20260725_0042"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "legal_deadlines",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("case_id", sa.Integer, sa.ForeignKey("legal_cases.id"), nullable=True),
        sa.Column("contract_id", sa.Integer, nullable=True),
        sa.Column("deadline_type", sa.String(32), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Asia/Shanghai"),
        sa.Column("owner_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("reminder_offsets_json", sa.Text, nullable=True),
        sa.Column("source_milestone_id", sa.Integer, nullable=True),
        sa.Column("is_historical", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_legal_deadlines_org_id", "legal_deadlines", ["organization_id"])
    op.create_index("ix_legal_deadlines_case_id", "legal_deadlines", ["case_id"])
    op.create_index("ix_legal_deadlines_status", "legal_deadlines", ["status"])
    op.create_index("ix_legal_deadlines_owner_id", "legal_deadlines", ["owner_id"])
    op.create_index("ix_legal_deadlines_deadline_at", "legal_deadlines", ["deadline_at"])

    op.create_table(
        "legal_portal_links",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("case_id", sa.Integer, sa.ForeignKey("legal_cases.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("token_prefix", sa.String(8), nullable=False),
        sa.Column("client_email", sa.String(256), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_permanent", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_access_count", sa.Integer, nullable=True),
        sa.Column("access_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("require_email_verification", sa.Integer, nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_legal_portal_links_org_id", "legal_portal_links", ["organization_id"])
    op.create_index("ix_legal_portal_links_case_id", "legal_portal_links", ["case_id"])
    op.create_index("ix_legal_portal_links_status", "legal_portal_links", ["status"])

    op.create_table(
        "legal_portal_link_items",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("portal_link_id", sa.Integer, sa.ForeignKey("legal_portal_links.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_type", sa.String(32), nullable=False),
        sa.Column("item_id", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_legal_portal_link_items_link_id", "legal_portal_link_items", ["portal_link_id"])

    op.create_table(
        "legal_portal_access_logs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("portal_link_id", sa.Integer, sa.ForeignKey("legal_portal_links.id"), nullable=False),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("accessed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("ip_hash", sa.String(64), nullable=True),
        sa.Column("user_agent_summary", sa.String(256), nullable=True),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("resource_type", sa.String(32), nullable=True),
        sa.Column("resource_id", sa.Integer, nullable=True),
        sa.Column("result", sa.String(16), nullable=False, server_default="success"),
    )
    op.create_index("ix_legal_portal_access_logs_link_id", "legal_portal_access_logs", ["portal_link_id"])
    op.create_index("ix_legal_portal_access_logs_org_id", "legal_portal_access_logs", ["organization_id"])

    op.create_table(
        "legal_case_members",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("case_id", sa.Integer, sa.ForeignKey("legal_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("case_role", sa.String(32), nullable=False),
        sa.Column("granted_by", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_legal_case_members_case_id", "legal_case_members", ["case_id"])
    op.create_index("ix_legal_case_members_user_id", "legal_case_members", ["user_id"])
    op.create_index("ix_legal_case_members_org_id", "legal_case_members", ["organization_id"])

    op.create_table(
        "legal_case_progress_updates",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("case_id", sa.Integer, sa.ForeignKey("legal_cases.id"), nullable=False),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("title", sa.String(128), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("next_steps", sa.Text, nullable=True),
        sa.Column("visibility", sa.String(16), nullable=False, server_default="internal"),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reviewed_by", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdraw_reason", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_legal_case_progress_updates_case_id", "legal_case_progress_updates", ["case_id"])
    op.create_index("ix_legal_case_progress_updates_status", "legal_case_progress_updates", ["status"])

    op.create_table(
        "legal_case_progress_reads",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("progress_update_id", sa.Integer, sa.ForeignKey("legal_case_progress_updates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("case_id", sa.Integer, sa.ForeignKey("legal_cases.id"), nullable=False),
        sa.Column("reader_type", sa.String(16), nullable=False),
        sa.Column("reader_id", sa.Integer, nullable=True),
        sa.Column("token_hash", sa.String(64), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_legal_case_progress_reads_update_id", "legal_case_progress_reads", ["progress_update_id"])


def downgrade():
    op.drop_table("legal_case_progress_reads")
    op.drop_table("legal_case_progress_updates")
    op.drop_table("legal_case_members")
    op.drop_table("legal_portal_access_logs")
    op.drop_table("legal_portal_link_items")
    op.drop_table("legal_portal_links")
    op.drop_table("legal_deadlines")
