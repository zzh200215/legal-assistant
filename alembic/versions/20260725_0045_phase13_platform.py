"""Phase 13 开放平台：developer_apps / api_keys / api_usage / webhooks / legal_async_jobs

Revision ID: 20260725_0045
Revises: 20260725_0044
Create Date: 2026-07-25
"""

from alembic import op
import sqlalchemy as sa

revision = "20260725_0045"
down_revision = "20260725_0044"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "developer_apps",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("ip_whitelist_json", sa.Text, nullable=True),
        sa.Column("webhook_url", sa.String(512), nullable=True),
        sa.Column("webhook_secret_hash", sa.String(64), nullable=True),
        sa.Column("subscribed_events_json", sa.Text, nullable=True),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("organization_id", "name", name="uq_developer_apps_org_name"),
    )
    op.create_index("ix_developer_apps_org_id", "developer_apps", ["organization_id"])
    op.create_index("ix_developer_apps_status", "developer_apps", ["status"])

    op.create_table(
        "developer_api_keys",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("app_id", sa.Integer, sa.ForeignKey("developer_apps.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("key_prefix", sa.String(12), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("transition_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_developer_api_keys_app_id", "developer_api_keys", ["app_id"])
    op.create_index("ix_developer_api_keys_org_id", "developer_api_keys", ["organization_id"])

    op.create_table(
        "developer_api_usage",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("app_id", sa.Integer, sa.ForeignKey("developer_apps.id"), nullable=False),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("endpoint", sa.String(256), nullable=False),
        sa.Column("method", sa.String(8), nullable=False),
        sa.Column("status_code", sa.Integer, nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column("tokens_used", sa.Integer, nullable=True, server_default="0"),
        sa.Column("stat_date", sa.String(10), nullable=False),
        sa.Column("stat_hour", sa.Integer, nullable=True),
        sa.Column("call_count", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_developer_api_usage_app_id", "developer_api_usage", ["app_id"])
    op.create_index("ix_developer_api_usage_stat_date", "developer_api_usage", ["stat_date"])

    op.create_table(
        "webhook_subscriptions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("app_id", sa.Integer, sa.ForeignKey("developer_apps.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("is_active", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_webhook_subscriptions_app_id", "webhook_subscriptions", ["app_id"])
    op.create_index("ix_webhook_subscriptions_event_type", "webhook_subscriptions", ["event_type"])

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("subscription_id", sa.Integer, sa.ForeignKey("webhook_subscriptions.id"), nullable=False),
        sa.Column("app_id", sa.Integer, sa.ForeignKey("developer_apps.id"), nullable=False),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("event_id", sa.String(128), nullable=False, unique=True),
        sa.Column("payload_hash", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_status", sa.Integer, nullable=True),
        sa.Column("response_body_snippet", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_webhook_deliveries_app_id", "webhook_deliveries", ["app_id"])
    op.create_index("ix_webhook_deliveries_status", "webhook_deliveries", ["status"])

    op.create_table(
        "legal_async_jobs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("case_id", sa.Integer, sa.ForeignKey("legal_cases.id"), nullable=True),
        sa.Column("resource_type", sa.String(64), nullable=True),
        sa.Column("resource_id", sa.Integer, nullable=True),
        sa.Column("job_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("idempotency_key", sa.String(128), nullable=True, unique=True),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("progress", sa.Integer, nullable=True),
        sa.Column("error_summary", sa.Text, nullable=True),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_summary", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_legal_async_jobs_org_id", "legal_async_jobs", ["organization_id"])
    op.create_index("ix_legal_async_jobs_status", "legal_async_jobs", ["status"])
    op.create_index("ix_legal_async_jobs_job_type", "legal_async_jobs", ["job_type"])
    op.create_index("ix_legal_async_jobs_resource", "legal_async_jobs", ["resource_type", "resource_id"])


def downgrade():
    op.drop_table("legal_async_jobs")
    op.drop_table("webhook_deliveries")
    op.drop_table("webhook_subscriptions")
    op.drop_table("developer_api_usage")
    op.drop_table("developer_api_keys")
    op.drop_table("developer_apps")
