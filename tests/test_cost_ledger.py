"""成本台账测试：Decimal 精度、来源去重、payment/refund 入账、追加式不可覆盖。"""
import unittest
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.cost_ledger import CostLedgerEntry
from app.services.cost_ledger_service import cost_ledger_service


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


class CostLedgerTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_amount_stored_as_decimal_not_float(self):
        entry = cost_ledger_service.record(
            db=self.db, tenant_id=1, entry_type="llm_call", direction="cost",
            amount=0.001234, source_type="llm_run", source_id="tu-1",
            scope="llm_cost", idempotency_key="llm:1")
        self.assertIsInstance(entry.amount, Decimal)
        self.assertEqual(entry.amount, Decimal("0.001234"))
        # 从 DB 读回仍是 Decimal
        self.db.expire_all()
        loaded = self.db.query(CostLedgerEntry).first()
        self.assertEqual(loaded.amount, Decimal("0.001234"))

    def test_source_dedup_single_entry(self):
        cost_ledger_service.record(
            db=self.db, tenant_id=1, entry_type="payment", direction="payment",
            amount="100.00", source_type="payment_record", source_id="p-1",
            scope="billing", idempotency_key="payment:p-1")
        # 同一来源重复处理 → 返回既有，不新增
        cost_ledger_service.record(
            db=self.db, tenant_id=1, entry_type="payment", direction="payment",
            amount="100.00", source_type="payment_record", source_id="p-1",
            scope="billing", idempotency_key="payment:p-1")
        self.assertEqual(self.db.query(CostLedgerEntry).count(), 1)

    def test_append_only_no_overwrite(self):
        e1 = cost_ledger_service.record(
            db=self.db, tenant_id=1, entry_type="payment", direction="payment",
            amount="50.00", source_type="payment_record", source_id="p-2",
            scope="billing", idempotency_key="payment:p-2")
        e2 = cost_ledger_service.record(
            db=self.db, tenant_id=1, entry_type="adjustment", direction="adjustment",
            amount="-50.00", source_type="payment_record", source_id="p-2",
            scope="billing", idempotency_key="adjust:p-2")
        self.assertNotEqual(e1.id, e2.id)
        self.assertNotEqual(e1.entry_id, e2.entry_id)
        total = cost_ledger_service.sum_by(self.db, tenant_id=1, direction="payment")
        self.assertEqual(total, Decimal("50.00"))

    def test_sum_by_direction_and_period(self):
        cost_ledger_service.record(
            db=self.db, tenant_id=7, entry_type="llm_call", direction="cost",
            amount="1.25", source_type="llm_run", source_id="t-1",
            billing_period="2026-08", scope="llm_cost", idempotency_key="llm:2")
        cost_ledger_service.record(
            db=self.db, tenant_id=7, entry_type="llm_call", direction="cost",
            amount="0.75", source_type="llm_run", source_id="t-2",
            billing_period="2026-08", scope="llm_cost", idempotency_key="llm:3")
        cost_ledger_service.record(
            db=self.db, tenant_id=7, entry_type="llm_call", direction="cost",
            amount="9.00", source_type="llm_run", source_id="t-3",
            billing_period="2026-09", scope="llm_cost", idempotency_key="llm:4")
        self.assertEqual(cost_ledger_service.sum_by(
            self.db, tenant_id=7, direction="cost", billing_period="2026-08"),
            Decimal("2.00"))
        self.assertEqual(cost_ledger_service.sum_by(
            self.db, tenant_id=7, direction="cost"), Decimal("11.00"))


if __name__ == "__main__":
    unittest.main()
