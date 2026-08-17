"""韧性测试：对账服务的乱序 / 断电恢复 / 跨 provider 隔离 / 最终一致闭环。

覆盖 app/services/billing/reconciliation_service.py：
- recover_stale_runs：worker 崩溃后租约过期 run 回置 pending（可重跑）；
- 断电恢复闭环：stale running → recover → 再次 run 成功；
- 跨 provider 游标独立：A provider 成功不影响 B；
- 退款超收（refund_mismatch）/ 超额收款（invoice_amount_mismatch）差异；
- 最终一致：webhook 事件补处理后，下一轮对账差异消失；
- 失败记账：run 异常 → failed + error_code（供任务重试）。
"""

import unittest
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.core.time import utc_now
from app.models.legal_billing import LegalInvoice, LegalPaymentRecord
from app.models.payment_event import PaymentEvent
from app.models.platform_payment import PlatformPayment
from app.models.reconciliation import ReconciliationDiscrepancy, ReconciliationRun
from app.services.billing.reconciliation_service import reconciliation_service


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


class ReconciliationResilienceTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _stale_run(self, *, provider="stripe", owner="dead-worker"):
        return reconciliation_service.get_or_create_run(
            db=self.db, run_date="2026-08-13", provider=provider,
            organization_id=None, owner=owner, ttl_seconds=1,
        )

    def _expire_lease(self, run: ReconciliationRun):
        run.lease_expires_at = utc_now() - timedelta(hours=1)
        self.db.commit()

    # ── 断电恢复：recover_stale_runs ────────────────────────────────────────
    def test_recover_stale_runs_resets_expired_lease(self):
        run = self._stale_run(owner="dead-1")
        self._expire_lease(run)
        recovered = reconciliation_service.recover_stale_runs(db=self.db)
        self.assertEqual([r.id for r in recovered], [run.id])
        self.db.refresh(run)
        self.assertEqual(run.status, "pending")
        self.assertIsNone(run.lease_owner)
        self.assertIsNone(run.lease_expires_at)
        self.assertEqual(run.error_code, "LEASE_EXPIRED")

    def test_recover_skips_live_runs(self):
        self._stale_run(owner="alive-1")  # 租约未过期
        recovered = reconciliation_service.recover_stale_runs(db=self.db)
        self.assertEqual(recovered, [])

    def test_crash_recovery_loop_completes(self):
        """断电恢复闭环：stale running → recover → 重新 run 成功。"""
        run = self._stale_run(owner="dead-2")
        self._expire_lease(run)
        reconciliation_service.recover_stale_runs(db=self.db)
        result = reconciliation_service.run(
            db=self.db, run_date="2026-08-13", provider="stripe", owner="worker-2")
        self.assertEqual(result["status"], "succeeded")
        # 成功后再次调度 → 幂等跳过
        result2 = reconciliation_service.run(
            db=self.db, run_date="2026-08-13", provider="stripe", owner="worker-3")
        self.assertEqual(result2["status"], "skipped")

    # ── 跨 provider 游标独立 ────────────────────────────────────────────────
    def test_provider_cursors_are_independent(self):
        reconciliation_service.run(db=self.db, run_date="2026-08-13", provider="stripe", owner="w1")
        # wechat 未被 stripe 的成功影响，可独立运行
        result = reconciliation_service.run(db=self.db, run_date="2026-08-13", provider="wechat", owner="w2")
        self.assertEqual(result["status"], "succeeded")
        runs = self.db.query(ReconciliationRun).all()
        self.assertEqual({r.provider for r in runs}, {"stripe", "wechat"})

    # ── 差异类型补充 ────────────────────────────────────────────────────────
    def test_refund_exceeds_payment_detected(self):
        payment = PlatformPayment(
            organization_id=1, user_id=1, plan_tier="pro",
            provider="stripe", provider_event_id="pi_1",
            amount=Decimal("100.00"), refunded_amount=Decimal("150.00"),
            status="refunded", currency="CNY",
            created_at=utc_now() - timedelta(days=1),
        )
        self.db.add(payment)
        self.db.commit()
        result = reconciliation_service.run(db=self.db, run_date="2026-08-13", provider="local", owner="t")
        self.assertGreaterEqual(result["discrepancies_found"], 1)
        disc = self.db.query(ReconciliationDiscrepancy).filter(
            ReconciliationDiscrepancy.discrepancy_type == "refund_mismatch").first()
        self.assertIsNotNone(disc)
        self.assertEqual(disc.actual_amount, Decimal("150.000000"))

    def test_overpaid_invoice_detected(self):
        invoice = LegalInvoice(organization_id=1, case_id=1, invoice_no="INV-O",
                               client_display_name="X", issue_date=date(2026, 8, 1),
                               subtotal=100, tax_amount=0, discount_amount=0,
                               total_amount=100, status="paid", payment_progress="fully_paid",
                               currency="CNY", created_by=1)
        self.db.add(invoice)
        self.db.commit()
        self.db.add(LegalPaymentRecord(
            invoice_id=invoice.id, organization_id=1,
            amount=Decimal("150.00"), currency="CNY",
            payment_method="provider", transaction_id="pay_1",
            provider="stripe", status="confirmed", recorded_by=1,
        ))
        self.db.commit()
        reconciliation_service.run(db=self.db, run_date="2026-08-13", provider="local", owner="t")
        disc = self.db.query(ReconciliationDiscrepancy).filter(
            ReconciliationDiscrepancy.discrepancy_type == "invoice_amount_mismatch").first()
        self.assertIsNotNone(disc)
        self.assertEqual(disc.expected_amount, Decimal("100.000000"))
        self.assertEqual(disc.actual_amount, Decimal("150.000000"))

    # ── 最终一致闭环 ────────────────────────────────────────────────────────
    def test_resolved_event_clears_discrepancy_next_run(self):
        stale = PaymentEvent(provider="stripe", provider_event_id="evt_x",
                             event_type="customer.subscription.updated",
                             raw_payload_hash="a" * 64, status="needs_reconciliation",
                             received_at=utc_now() - timedelta(hours=3))
        self.db.add(stale)
        self.db.commit()
        first = reconciliation_service.run(db=self.db, run_date="2026-08-14", provider="stripe", owner="w1")
        self.assertGreaterEqual(first["discrepancies_found"], 1)
        # 事件补处理后（乱序/延迟到达已解决）→ 下一轮对账不再报该差异
        stale.status = "completed"
        self.db.commit()
        second = reconciliation_service.run(db=self.db, run_date="2026-08-15", provider="stripe", owner="w2")
        self.assertEqual(second["status"], "succeeded")
        self.assertEqual(self.db.query(ReconciliationDiscrepancy).filter(
            ReconciliationDiscrepancy.discrepancy_type == "webhook_pending").count(), 1)  # 仅第一轮的

    # ── 失败记账 ────────────────────────────────────────────────────────────
    def test_run_failure_recorded_for_retry(self):
        invoice = LegalInvoice(organization_id=1, case_id=1, invoice_no="INV-F",
                               client_display_name="X", issue_date=date(2026, 8, 1),
                               subtotal=100, tax_amount=0, discount_amount=0,
                               total_amount=100, status="paid", payment_progress="fully_paid",
                               currency="CNY", created_by=1)
        self.db.add(invoice)
        self.db.commit()
        with (
            patch.object(reconciliation_service, "_paid_total", side_effect=RuntimeError("db hiccup")),
            self.assertRaises(RuntimeError),
        ):
            reconciliation_service.run(db=self.db, run_date="2026-08-13", provider="stripe", owner="w1")
        run = self.db.query(ReconciliationRun).filter(
            ReconciliationRun.provider == "stripe").order_by(ReconciliationRun.id.desc()).first()
        self.assertEqual(run.status, "failed")
        self.assertEqual(run.error_code, "RuntimeError")
        # 失败 run 可被 recover 后重跑（恢复闭环）
        run.status = "running"
        run.lease_expires_at = utc_now() - timedelta(hours=1)
        self.db.commit()
        self.assertGreaterEqual(len(reconciliation_service.recover_stale_runs(db=self.db)), 1)


if __name__ == "__main__":
    unittest.main()
