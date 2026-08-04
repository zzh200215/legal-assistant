"""Phase 14 安全审计 / 通知 / 引导进度

Revision ID: 20260725_0046
Revises: 20260725_0045
Create Date: 2026-07-25
"""

from alembic import op
import sqlalchemy as sa

revision = "20260725_0046"
down_revision = "20260725_0045"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "security_audit_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(64), nullable=True),
        sa.Column("target_type", sa.String(64), nullable=True),
        sa.Column("target_id", sa.String(64), nullable=True),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("detail_json_hash", sa.String(64), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seq_no", sa.Integer, nullable=False, unique=True),
        sa.Column("prev_hash", sa.String(64), nullable=True),
        sa.Column("current_hash", sa.String(64), nullable=False),
    )
    op.create_index("ix_security_audit_events_org_id", "security_audit_events", ["organization_id"])
    op.create_index("ix_security_audit_events_event_type", "security_audit_events", ["event_type"])
    op.create_index("ix_security_audit_events_occurred_at", "security_audit_events", ["occurred_at"])

    op.create_table(
        "legal_notification_preferences",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("channels_json", sa.Text, nullable=True),
        sa.Column("mute_start", sa.String(5), nullable=True),
        sa.Column("mute_end", sa.String(5), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Asia/Shanghai"),
        sa.Column("delegate_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("summary_frequency", sa.String(16), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_legal_notification_preferences_user_id", "legal_notification_preferences", ["user_id"])
    op.create_index("ix_legal_notification_preferences_org_id", "legal_notification_preferences", ["organization_id"])

    op.create_table(
        "legal_notification_policies",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("case_id", sa.Integer, sa.ForeignKey("legal_cases.id"), nullable=False),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("escalation_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("advance_days_json", sa.Text, nullable=True),
        sa.Column("is_active", sa.Integer, nullable=False, server_default="1"),
        sa.Column("updated_by", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_legal_notification_policies_case_id", "legal_notification_policies", ["case_id"])

    op.create_table(
        "legal_notification_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("case_id", sa.Integer, sa.ForeignKey("legal_cases.id"), nullable=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("body", sa.Text, nullable=True),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("reference_type", sa.String(64), nullable=True),
        sa.Column("reference_id", sa.Integer, nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_legal_notification_events_user_id", "legal_notification_events", ["user_id"])
    op.create_index("ix_legal_notification_events_org_id", "legal_notification_events", ["organization_id"])
    op.create_index("ix_legal_notification_events_status", "legal_notification_events", ["status"])

    op.create_table(
        "organization_onboarding_progress",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False, unique=True),
        sa.Column("user_role", sa.String(32), nullable=True),
        sa.Column("completed_steps_json", sa.Text, nullable=True),
        sa.Column("skipped_steps_json", sa.Text, nullable=True),
        sa.Column("version", sa.String(16), nullable=False, server_default="v3.0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_organization_onboarding_progress_org_id", "organization_onboarding_progress", ["organization_id"])


def downgrade():
    op.drop_table("organization_onboarding_progress")
    op.drop_table("legal_notification_events")
    op.drop_table("legal_notification_policies")
    op.drop_table("legal_notification_preferences")
    op.drop_table("security_audit_events")
