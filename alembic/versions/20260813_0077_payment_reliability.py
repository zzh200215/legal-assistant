"""P0 订阅/计费/支付可靠性：快照、事件台账、成本台账、配额预留、对账。

- subscription_plans +price_version/currency；新表 subscription_plan_versions。
- user_subscriptions +plan_version/idempotency_key(UNIQUE)。
- quota_usages +UNIQUE(user_id, year_month)（先按最新去重）。
- legal_invoices +currency/price_snapshot_json/tax_snapshot_json/snapshot_hash。
- platform_payments +idempotency_key(UNIQUE)/provider/provider_event_id/refunded_amount。
- 新表 payment_events / cost_ledger / usage_reservations /
  reconciliation_runs + reconciliation_discrepancies。
- 回填：price_version=1、currency='CNY'、legacy 幂等键、套餐版本快照（幂等）。

Revision ID: 20260813_0077
Revises: 20260813_0076
Create Date: 2026-08-13
"""
from alembic import op
import sqlalchemy as sa

revision = "20260813_0077"
down_revision = "20260813_0076"
branch_labels = None
depends_on = None


# ── subscription 扩展 ─────────────────────────────────────────────────────────

def _extend_subscription() -> None:
    with op.batch_alter_table("subscription_plans") as batch_op:
        batch_op.add_column(sa.Column("price_version", sa.Integer(), nullable=False, server_default="1"))
        batch_op.add_column(sa.Column("currency", sa.String(8), nullable=False, server_default="CNY"))

    op.create_table(
        "subscription_plan_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("tier", sa.String(32), nullable=False),
        sa.Column("price_version", sa.Integer(), nullable=False),
        sa.Column("price_monthly", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("quota_consultation", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("quota_review", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("quota_draft", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("currency", sa.String(8), nullable=False, server_default="CNY"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.UniqueConstraint("plan_id", "price_version", name="uq_plan_versions_plan_id_version"),
        sa.ForeignKeyConstraint(["plan_id"], ["subscription_plans.id"]),
    )
    op.create_index("ix_subscription_plan_versions_plan_id", "subscription_plan_versions", ["plan_id"])
    op.create_index("ix_subscription_plan_versions_tier", "subscription_plan_versions", ["tier"])

    with op.batch_alter_table("user_subscriptions") as batch_op:
        batch_op.add_column(sa.Column("plan_version", sa.Integer(), nullable=False, server_default="1"))
        batch_op.add_column(sa.Column("idempotency_key", sa.String(128), nullable=True))
        batch_op.create_index("ix_user_subscriptions_idempotency_key", ["idempotency_key"], unique=True)


# ── quota_usages 唯一约束（先去重）────────────────────────────────────────────

def _quota_usage_unique(bind) -> None:
    from sqlalchemy import Connection, text

    def _run(conn) -> None:
        # 保留每组最新 id，避免 UNIQUE(user_id, year_month) 因历史重复而失败
        conn.execute(text(
            "DELETE FROM quota_usages WHERE id NOT IN ("
            "SELECT keep.id FROM (SELECT MAX(id) AS id FROM quota_usages "
            "GROUP BY user_id, year_month) keep)"
        ))

    if isinstance(bind, Connection):
        _run(bind)
    else:
        with bind.connect() as conn:
            _run(conn)
            conn.commit()

    with op.batch_alter_table("quota_usages") as batch_op:
        batch_op.create_unique_constraint("uq_quota_usages_user_month", ["user_id", "year_month"])


# ── 新表 ──────────────────────────────────────────────────────────────────────

def _create_payment_events() -> None:
    op.create_table(
        "payment_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_event_id", sa.String(128), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("raw_payload_hash", sa.String(64), nullable=False),
        sa.Column("sanitized_payload_json", sa.Text(), nullable=True),
        sa.Column("object_type", sa.String(64), nullable=True),
        sa.Column("object_id", sa.String(128), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_by", sa.String(128), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.UniqueConstraint("provider", "provider_event_id",
                            name="uq_payment_events_provider_event_id"),
    )
    op.create_index("ix_payment_events_provider", "payment_events", ["provider"])
    op.create_index("ix_payment_events_event_type", "payment_events", ["event_type"])
    op.create_index("ix_payment_events_object_id", "payment_events", ["object_id"])
    op.create_index("ix_payment_events_received_at", "payment_events", ["received_at"])
    op.create_index("ix_payment_events_status", "payment_events", ["status"])
    op.create_index("ix_payment_events_next_retry_at", "payment_events", ["next_retry_at"])
    op.create_index("ix_payment_events_claim_expires_at", "payment_events", ["claim_expires_at"])


def _create_cost_ledger() -> None:
    op.create_table(
        "cost_ledger",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("entry_id", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("entry_type", sa.String(64), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("amount", sa.Numeric(18, 6), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="CNY"),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=True),
        sa.Column("unit", sa.String(64), nullable=True),
        sa.Column("unit_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("source_type", sa.String(64), nullable=True),
        sa.Column("source_id", sa.String(128), nullable=True),
        sa.Column("billing_period", sa.String(7), nullable=True),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("metadata_summary", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.UniqueConstraint("scope", "idempotency_key", name="uq_cost_ledger_scope_key"),
    )
    op.create_index("ix_cost_ledger_entry_id", "cost_ledger", ["entry_id"])
    op.create_index("ix_cost_ledger_tenant_id", "cost_ledger", ["tenant_id"])
    op.create_index("ix_cost_ledger_source_id", "cost_ledger", ["source_id"])
    op.create_index("ix_cost_ledger_created_at", "cost_ledger", ["created_at"])


def _create_usage_reservations() -> None:
    op.create_table(
        "usage_reservations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("quota_type", sa.String(32), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="reserved"),
        sa.Column("usage_event_id", sa.String(128), nullable=False),
        sa.Column("billing_period", sa.String(7), nullable=False),
        sa.Column("source_type", sa.String(64), nullable=True),
        sa.Column("source_id", sa.String(128), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.UniqueConstraint("usage_event_id", name="uq_usage_reservations_event_id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_index("ix_usage_reservations_user_id", "usage_reservations", ["user_id"])
    op.create_index("ix_usage_reservations_status", "usage_reservations", ["status"])
    op.create_index("ix_usage_reservations_source_id", "usage_reservations", ["source_id"])


def _create_reconciliation() -> None:
    op.create_table(
        "reconciliation_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_date", sa.String(10), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("cursor_json", sa.Text(), nullable=True),
        sa.Column("checkpoint_json", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("discrepancies_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    )
    op.create_index("ix_reconciliation_runs_run_date", "reconciliation_runs", ["run_date"])
    op.create_index("ix_reconciliation_runs_status", "reconciliation_runs", ["status"])
    op.create_index("ix_reconciliation_runs_lease_expires_at", "reconciliation_runs", ["lease_expires_at"])

    op.create_table(
        "reconciliation_discrepancies",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("discrepancy_type", sa.String(64), nullable=False),
        sa.Column("local_reference", sa.String(256), nullable=True),
        sa.Column("provider_reference", sa.String(256), nullable=True),
        sa.Column("expected_amount", sa.Numeric(18, 6), nullable=True),
        sa.Column("actual_amount", sa.Numeric(18, 6), nullable=True),
        sa.Column("currency", sa.String(8), nullable=True),
        sa.Column("expected_status", sa.String(32), nullable=True),
        sa.Column("actual_status", sa.String(32), nullable=True),
        sa.Column("severity", sa.String(16), nullable=False, server_default="medium"),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("recommended_action", sa.String(512), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
    )
    op.create_index("ix_reconciliation_discrepancies_run_id", "reconciliation_discrepancies", ["run_id"])
    op.create_index("ix_reconciliation_discrepancies_type", "reconciliation_discrepancies", ["discrepancy_type"])


# ── legal_invoices / platform_payments 扩展 ───────────────────────────────────

def _extend_invoices_and_payments() -> None:
    with op.batch_alter_table("legal_invoices") as batch_op:
        batch_op.add_column(sa.Column("currency", sa.String(8), nullable=False, server_default="CNY"))
        batch_op.add_column(sa.Column("price_snapshot_json", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("tax_snapshot_json", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("snapshot_hash", sa.String(64), nullable=True))

    with op.batch_alter_table("legal_refund_records") as batch_op:
        batch_op.add_column(sa.Column("provider_refund_id", sa.String(128), nullable=True))
        batch_op.create_index("ix_legal_refund_records_provider_refund_id", ["provider_refund_id"])

    with op.batch_alter_table("platform_payments") as batch_op:
        batch_op.add_column(sa.Column("idempotency_key", sa.String(128), nullable=True))
        batch_op.add_column(sa.Column("provider", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("provider_event_id", sa.String(128), nullable=True))
        batch_op.add_column(sa.Column("refunded_amount", sa.Numeric(14, 2), nullable=False, server_default="0"))
        batch_op.create_index("ix_platform_payments_idempotency_key", ["idempotency_key"], unique=True)
        batch_op.create_index("ix_platform_payments_provider_event_id", ["provider_event_id"])


# ── 回填 ──────────────────────────────────────────────────────────────────────

def _backfill(bind) -> None:
    """既有数据补齐版本/币种/幂等键/套餐版本快照（幂等，不臆造数据）。"""
    from sqlalchemy import Connection, text

    def _run(conn) -> None:
        conn.execute(text(
            "UPDATE subscription_plans SET price_version = 1 WHERE price_version IS NULL"
        ))
        conn.execute(text(
            "UPDATE subscription_plans SET currency = 'CNY' WHERE currency IS NULL OR currency = ''"
        ))
        conn.execute(text(
            "UPDATE user_subscriptions SET plan_version = 1 WHERE plan_version IS NULL"
        ))
        # 幂等键回填
        subs = sa.table("user_subscriptions",
                        sa.column("id", sa.Integer),
                        sa.column("idempotency_key", sa.String(128)))
        conn.execute(sa.update(subs).where(subs.c.idempotency_key.is_(None))
                     .values(idempotency_key=sa.func.concat("legacy:sub:", subs.c.id)))
        pays = sa.table("platform_payments",
                        sa.column("id", sa.Integer),
                        sa.column("idempotency_key", sa.String(128)))
        conn.execute(sa.update(pays).where(pays.c.idempotency_key.is_(None))
                     .values(idempotency_key=sa.func.concat("legacy:pay:", pays.c.id)))
        # 从当前计划生成套餐版本快照（版本 1，可移植 check-then-insert）
        plans_rows = conn.execute(sa.text(
            "SELECT id, tier, price_version, price_monthly, quota_consultation, "
            "quota_review, quota_draft, currency FROM subscription_plans"
        )).fetchall()
        for pid, tier, pv, price, qc, qr, qd, cur in plans_rows:
            exists = conn.execute(sa.text(
                "SELECT 1 FROM subscription_plan_versions WHERE plan_id = :pid AND price_version = :pv"
            ), {"pid": pid, "pv": pv}).scalar()
            if exists:
                continue
            conn.execute(sa.text(
                "INSERT INTO subscription_plan_versions "
                "(plan_id, tier, price_version, price_monthly, quota_consultation, quota_review, "
                " quota_draft, currency) "
                "VALUES (:pid, :tier, :pv, :price, :qc, :qr, :qd, :cur)"
            ), {"pid": pid, "tier": tier, "pv": pv, "price": price,
                "qc": qc, "qr": qr, "qd": qd, "cur": cur})

    if isinstance(bind, Connection):
        _run(bind)
    else:
        with bind.connect() as conn:
            _run(conn)
            conn.commit()


def upgrade() -> None:
    _extend_subscription()
    _quota_usage_unique(op.get_bind())
    _create_payment_events()
    _create_cost_ledger()
    _create_usage_reservations()
    _create_reconciliation()
    _extend_invoices_and_payments()
    _backfill(op.get_bind())


def downgrade() -> None:
    with op.batch_alter_table("platform_payments") as batch_op:
        batch_op.drop_index("ix_platform_payments_provider_event_id")
        batch_op.drop_index("ix_platform_payments_idempotency_key")
        batch_op.drop_column("refunded_amount")
        batch_op.drop_column("provider_event_id")
        batch_op.drop_column("provider")
        batch_op.drop_column("idempotency_key")
    with op.batch_alter_table("legal_invoices") as batch_op:
        batch_op.drop_column("snapshot_hash")
        batch_op.drop_column("tax_snapshot_json")
        batch_op.drop_column("price_snapshot_json")
        batch_op.drop_column("currency")
    with op.batch_alter_table("legal_refund_records") as batch_op:
        batch_op.drop_index("ix_legal_refund_records_provider_refund_id")
        batch_op.drop_column("provider_refund_id")
    op.drop_table("reconciliation_discrepancies")
    op.drop_table("reconciliation_runs")
    op.drop_table("usage_reservations")
    op.drop_table("cost_ledger")
    op.drop_table("payment_events")
    with op.batch_alter_table("quota_usages") as batch_op:
        batch_op.drop_constraint("uq_quota_usages_user_month", type_="unique")
    with op.batch_alter_table("user_subscriptions") as batch_op:
        batch_op.drop_index("ix_user_subscriptions_idempotency_key")
        batch_op.drop_column("idempotency_key")
        batch_op.drop_column("plan_version")
    op.drop_table("subscription_plan_versions")
    with op.batch_alter_table("subscription_plans") as batch_op:
        batch_op.drop_column("currency")
        batch_op.drop_column("price_version")
