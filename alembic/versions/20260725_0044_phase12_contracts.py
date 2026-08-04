"""Phase 12 合同台账 / 版本 / 条款 / 里程碑 / 电子签名 / 审查策略

Revision ID: 20260725_0044
Revises: 20260725_0043
Create Date: 2026-07-25
"""

from alembic import op
import sqlalchemy as sa

revision = "20260725_0044"
down_revision = "20260725_0043"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "legal_contracts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("case_id", sa.Integer, sa.ForeignKey("legal_cases.id"), nullable=True),
        sa.Column("contract_no", sa.String(64), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("counterparty", sa.String(256), nullable=True),
        sa.Column("contract_type", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("current_version_id", sa.Integer, nullable=True),
        sa.Column("responsible_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("risk_level", sa.String(16), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("organization_id", "contract_no", name="uq_legal_contracts_org_no"),
    )
    op.create_index("ix_legal_contracts_org_id", "legal_contracts", ["organization_id"])
    op.create_index("ix_legal_contracts_case_id", "legal_contracts", ["case_id"])
    op.create_index("ix_legal_contracts_status", "legal_contracts", ["status"])

    op.create_table(
        "legal_contract_versions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("contract_id", sa.Integer, sa.ForeignKey("legal_contracts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("version_no", sa.Integer, nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False, server_default="document"),
        sa.Column("source_document_id", sa.Integer, sa.ForeignKey("documents.id"), nullable=True),
        sa.Column("source_review_id", sa.Integer, sa.ForeignKey("legal_contract_reviews.id"), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("text_snapshot", sa.Text, nullable=True),
        sa.Column("parse_status", sa.String(24), nullable=False, server_default="uploading"),
        sa.Column("parse_confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("is_current", sa.Integer, nullable=False, server_default="0"),
        sa.Column("version_note", sa.Text, nullable=True),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("contract_id", "version_no", name="uq_legal_contract_versions_no"),
    )
    op.create_index("ix_legal_contract_versions_contract_id", "legal_contract_versions", ["contract_id"])
    op.create_index("ix_legal_contract_versions_parse_status", "legal_contract_versions", ["parse_status"])
    op.create_index("ix_legal_contract_versions_is_current", "legal_contract_versions", ["is_current"])

    op.create_table(
        "legal_contract_clauses",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("contract_version_id", sa.Integer, sa.ForeignKey("legal_contract_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("clause_no", sa.String(32), nullable=True),
        sa.Column("clause_title", sa.String(256), nullable=True),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("chapter", sa.String(128), nullable=True),
        sa.Column("sequence", sa.Integer, nullable=False, server_default="0"),
        sa.Column("parse_confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_legal_contract_clauses_version_id", "legal_contract_clauses", ["contract_version_id"])

    op.create_table(
        "legal_contract_milestones",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("contract_id", sa.Integer, sa.ForeignKey("legal_contracts.id"), nullable=False),
        sa.Column("contract_version_id", sa.Integer, sa.ForeignKey("legal_contract_versions.id"), nullable=True),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("milestone_type", sa.String(32), nullable=False),
        sa.Column("raw_text", sa.Text, nullable=True),
        sa.Column("standard_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_clause_no", sa.String(32), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending_confirmation"),
        sa.Column("confirmed_by", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_legal_contract_milestones_contract_id", "legal_contract_milestones", ["contract_id"])
    op.create_index("ix_legal_contract_milestones_status", "legal_contract_milestones", ["status"])
    op.create_index("ix_legal_contract_milestones_org_id", "legal_contract_milestones", ["organization_id"])

    op.create_table(
        "legal_sign_requests",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("contract_id", sa.Integer, sa.ForeignKey("legal_contracts.id"), nullable=False),
        sa.Column("contract_version_id", sa.Integer, sa.ForeignKey("legal_contract_versions.id"), nullable=False),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_request_id", sa.String(128), nullable=True, unique=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("replaces_request_id", sa.Integer, sa.ForeignKey("legal_sign_requests.id"), nullable=True),
        sa.Column("initiated_by", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_legal_sign_requests_contract_id", "legal_sign_requests", ["contract_id"])
    op.create_index("ix_legal_sign_requests_org_id", "legal_sign_requests", ["organization_id"])
    op.create_index("ix_legal_sign_requests_status", "legal_sign_requests", ["status"])

    op.create_table(
        "legal_sign_parties",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("sign_request_id", sa.Integer, sa.ForeignKey("legal_sign_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("email_hash", sa.String(64), nullable=True),
        sa.Column("phone_masked", sa.String(32), nullable=True),
        sa.Column("sign_order", sa.Integer, nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reject_reason", sa.Text, nullable=True),
        sa.Column("provider_sign_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_legal_sign_parties_request_id", "legal_sign_parties", ["sign_request_id"])

    op.create_table(
        "legal_sign_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("sign_request_id", sa.Integer, sa.ForeignKey("legal_sign_requests.id"), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("provider_event_id", sa.String(128), nullable=False, unique=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_payload_hash", sa.String(64), nullable=True),
        sa.Column("party_id", sa.Integer, sa.ForeignKey("legal_sign_parties.id"), nullable=True),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_legal_sign_events_request_id", "legal_sign_events", ["sign_request_id"])
    op.create_index("ix_legal_sign_events_event_type", "legal_sign_events", ["event_type"])

    op.create_table(
        "legal_review_policies",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("party_role", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("contract_type", sa.String(64), nullable=True),
        sa.Column("scenario", sa.String(128), nullable=True),
        sa.Column("amount_threshold_min", sa.Numeric(14, 2), nullable=True),
        sa.Column("amount_threshold_max", sa.Numeric(14, 2), nullable=True),
        sa.Column("risk_preference", sa.String(16), nullable=False, server_default="standard"),
        sa.Column("required_clauses_json", sa.Text, nullable=True),
        sa.Column("focus_points", sa.Text, nullable=True),
        sa.Column("is_active", sa.Integer, nullable=False, server_default="1"),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_legal_review_policies_org_id", "legal_review_policies", ["organization_id"])
    op.create_index("ix_legal_review_policies_is_active", "legal_review_policies", ["is_active"])

    op.create_table(
        "legal_review_policy_versions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("policy_id", sa.Integer, sa.ForeignKey("legal_review_policies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("config_snapshot", sa.Text, nullable=False),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("policy_id", "version", name="uq_legal_review_policy_versions"),
    )
    op.create_index("ix_legal_review_policy_versions_policy_id", "legal_review_policy_versions", ["policy_id"])


def downgrade():
    op.drop_table("legal_review_policy_versions")
    op.drop_table("legal_review_policies")
    op.drop_table("legal_sign_events")
    op.drop_table("legal_sign_parties")
    op.drop_table("legal_sign_requests")
    op.drop_table("legal_contract_milestones")
    op.drop_table("legal_contract_clauses")
    op.drop_table("legal_contract_versions")
    op.drop_table("legal_contracts")
