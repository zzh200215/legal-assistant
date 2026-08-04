"""Phase 11 计时计费表：legal_time_entries / legal_billing_rules / legal_invoices / legal_invoice_items / payment / refund / collection

Revision ID: 20260725_0042
Revises: 20260725_0041
Create Date: 2026-07-25
"""

from alembic import op
import sqlalchemy as sa

revision = "20260725_0042"
down_revision = "20260725_0041b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "legal_billing_rules",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("case_id", sa.Integer, sa.ForeignKey("legal_cases.id"), nullable=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("billing_mode", sa.String(16), nullable=False),
        sa.Column("hourly_rate", sa.Numeric(12, 2), nullable=True),
        sa.Column("fixed_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(8), nullable=False, server_default="CNY"),
        sa.Column("effective_from", sa.Date, nullable=True),
        sa.Column("effective_to", sa.Date, nullable=True),
        sa.Column("is_active", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_legal_billing_rules_org_id", "legal_billing_rules", ["organization_id"])
    op.create_index("ix_legal_billing_rules_case_id", "legal_billing_rules", ["case_id"])

    op.create_table(
        "legal_time_entries",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("case_id", sa.Integer, sa.ForeignKey("legal_cases.id"), nullable=False),
        sa.Column("operator_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("billing_rule_id", sa.Integer, sa.ForeignKey("legal_billing_rules.id"), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_minutes", sa.Integer, nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("description", sa.String(500), nullable=False, server_default=""),
        sa.Column("hourly_rate", sa.Numeric(12, 2), nullable=True),
        sa.Column("billed_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("billable", sa.Integer, nullable=False, server_default="0"),
        sa.Column("confirmed_by", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(64), nullable=True, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_legal_time_entries_org_id", "legal_time_entries", ["organization_id"])
    op.create_index("ix_legal_time_entries_case_id", "legal_time_entries", ["case_id"])
    op.create_index("ix_legal_time_entries_operator_id", "legal_time_entries", ["operator_id"])
    op.create_index("ix_legal_time_entries_status", "legal_time_entries", ["status"])

    op.create_table(
        "legal_invoices",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("case_id", sa.Integer, sa.ForeignKey("legal_cases.id"), nullable=False),
        sa.Column("invoice_no", sa.String(64), nullable=False, unique=True),
        sa.Column("client_display_name", sa.String(256), nullable=False),
        sa.Column("client_contact", sa.String(256), nullable=True),
        sa.Column("issue_date", sa.Date, nullable=False),
        sa.Column("due_date", sa.Date, nullable=True),
        sa.Column("billing_period_start", sa.Date, nullable=True),
        sa.Column("billing_period_end", sa.Date, nullable=True),
        sa.Column("subtotal", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("tax_amount", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("discount_amount", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("total_amount", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("payment_progress", sa.String(16), nullable=False, server_default="unpaid"),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("pdf_path", sa.String(512), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("void_reason", sa.Text, nullable=True),
        sa.Column("original_invoice_id", sa.Integer, sa.ForeignKey("legal_invoices.id"), nullable=True),
        sa.Column("collection_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("idempotency_key", sa.String(64), nullable=True, unique=True),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_legal_invoices_org_id", "legal_invoices", ["organization_id"])
    op.create_index("ix_legal_invoices_case_id", "legal_invoices", ["case_id"])
    op.create_index("ix_legal_invoices_status", "legal_invoices", ["status"])
    op.create_index("ix_legal_invoices_payment_progress", "legal_invoices", ["payment_progress"])

    op.create_table(
        "legal_invoice_items",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("invoice_id", sa.Integer, sa.ForeignKey("legal_invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("time_entry_id", sa.Integer, sa.ForeignKey("legal_time_entries.id"), nullable=True),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("duration_minutes", sa.Integer, nullable=True),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("quantity", sa.Numeric(10, 2), nullable=False, server_default="1"),
        sa.Column("discount_rate", sa.Numeric(5, 4), nullable=False, server_default="0"),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_legal_invoice_items_invoice_id", "legal_invoice_items", ["invoice_id"])

    op.create_table(
        "legal_payment_records",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("invoice_id", sa.Integer, sa.ForeignKey("legal_invoices.id"), nullable=False),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="CNY"),
        sa.Column("payment_method", sa.String(16), nullable=False),
        sa.Column("transaction_id", sa.String(128), nullable=True),
        sa.Column("provider", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="confirmed"),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("voucher_document_id", sa.Integer, sa.ForeignKey("documents.id"), nullable=True),
        sa.Column("recorded_by", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_legal_payment_records_invoice_id", "legal_payment_records", ["invoice_id"])
    op.create_index("ix_legal_payment_records_org_id", "legal_payment_records", ["organization_id"])

    op.create_table(
        "legal_refund_records",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("invoice_id", sa.Integer, sa.ForeignKey("legal_invoices.id"), nullable=False),
        sa.Column("payment_record_id", sa.Integer, sa.ForeignKey("legal_payment_records.id"), nullable=True),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("recorded_by", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("approved_by", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_legal_refund_records_invoice_id", "legal_refund_records", ["invoice_id"])

    op.create_table(
        "legal_collection_reminders",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("invoice_id", sa.Integer, sa.ForeignKey("legal_invoices.id"), nullable=False),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_legal_collection_reminders_invoice_id", "legal_collection_reminders", ["invoice_id"])


def downgrade():
    op.drop_table("legal_collection_reminders")
    op.drop_table("legal_refund_records")
    op.drop_table("legal_payment_records")
    op.drop_table("legal_invoice_items")
    op.drop_table("legal_invoices")
    op.drop_table("legal_time_entries")
    op.drop_table("legal_billing_rules")
