"""每日对账测试：识别差异、重复调度幂等、不静默修改财务记录。"""
import unittest
from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.core.time import utc_now
from app.models.legal_billing import LegalInvoice
from app.models.payment_event import PaymentEvent
from app.models.reconciliation import ReconciliationDiscrepancy, ReconciliationRun
from app.services.billing.reconciliation_service import reconciliation_service


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


class ReconciliationServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_detects_webhook_pending_discrepancy(self):
        stale = PaymentEvent(provider="stripe", provider_event_id="evt_old",
                             event_type="customer.subscription.updated",
                             raw_payload_hash="x" * 64, status="needs_reconciliation",
                             received_at=utc_now() - timedelta(hours=2))
        self.db.add(stale)
        self.db.commit()
        result = reconciliation_service.run(
            db=self.db, run_date="2026-08-13", provider="local", owner="test")
        self.assertEqual(result["status"], "succeeded")
        self.assertGreaterEqual(result["discrepancies_found"], 1)
        disc = self.db.query(ReconciliationDiscrepancy).filter(
            ReconciliationDiscrepancy.discrepancy_type == "webhook_pending").first()
        self.assertIsNotNone(disc)
        self.assertEqual(disc.status, "open")

    def test_detects_invoice_amount_mismatch(self):
        invoice = LegalInvoice(organization_id=1, case_id=1, invoice_no="INV-M",
                               client_display_name="X", issue_date=date(2026, 8, 1),
                               subtotal=100, tax_amount=0, discount_amount=0,
                               total_amount=100, status="paid", payment_progress="fully_paid",
                               currency="CNY", created_by=1)
        self.db.add(invoice)
        self.db.commit()  # paid 但无付款记录 → status_mismatch
        result = reconciliation_service.run(
            db=self.db, run_date="2026-08-13", provider="local", owner="test")
        self.assertGreaterEqual(result["discrepancies_found"], 1)
        disc = self.db.query(ReconciliationDiscrepancy).filter(
            ReconciliationDiscrepancy.discrepancy_type == "invoice_status_mismatch").first()
        self.assertIsNotNone(disc)
        # 不自动修改财务记录：发票仍 paid
        self.db.refresh(invoice)
        self.assertEqual(invoice.status, "paid")

    def test_repeat_run_skipped(self):
        # 先跑一次（成功）
        reconciliation_service.run(db=self.db, run_date="2026-08-13", provider="local", owner="t1")
        # 再次调度 → 幂等跳过，不重复生成差异
        result = reconciliation_service.run(
            db=self.db, run_date="2026-08-13", provider="local", owner="t2")
        self.assertEqual(result.get("status"), "skipped")
        self.assertEqual(self.db.query(ReconciliationRun).filter(
            ReconciliationRun.status == "succeeded").count(), 1)

    def test_run_records_cursor_and_lease(self):
        run = reconciliation_service.get_or_create_run(
            db=self.db, run_date="2026-08-13", provider="local",
            organization_id=None, owner="worker", ttl_seconds=900)
        self.assertIsNotNone(run)
        self.assertEqual(run.status, "running")
        self.assertEqual(run.lease_owner, "worker")
        self.assertIsNotNone(run.lease_expires_at)


if __name__ == "__main__":
    unittest.main()
