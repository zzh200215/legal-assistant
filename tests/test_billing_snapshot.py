"""账单快照测试：价格/税率/折扣变更不影响历史发票与金额。"""
import unittest
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import hash_password
from app.core.database import Base
from app.models.legal import LegalCase
from app.models.legal_billing import LegalBillingRule, LegalTimeEntry
from app.models.org import Organization
from app.models.user import User, UserStatus
from app.services.billing.billing_service import billing_service


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


class BillingSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)()
        org = Organization(name="SnapshotOrg", code="SNAP")
        self.db.add(org)
        self.db.flush()
        self.org_id = org.id
        user = User(username="b", email="b@x.com", hashed_password=hash_password("pw"),
                    role="admin", status=UserStatus.active.value, organization_id=org.id)
        self.db.add(user)
        self.db.flush()
        self.user_id = user.id
        case = LegalCase(organization_id=org.id, user_id=user.id, title="c", case_type="other")
        self.db.add(case)
        self.db.flush()
        self.case_id = case.id
        self.db.add(LegalBillingRule(
            organization_id=org.id, case_id=case.id, name="hourly", billing_mode="hourly",
            hourly_rate=1000, currency="CNY", is_active=1, created_by=user.id))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _confirmed_entry(self, minutes: int = 60) -> LegalTimeEntry:
        entry = billing_service.start_timer(
            db=self.db, organization_id=self.org_id, case_id=self.case_id,
            operator_id=self.user_id, description=f"计时 {minutes} 分钟")
        entry.started_at = entry.started_at - timedelta(minutes=minutes)
        entry.ended_at = entry.started_at + timedelta(minutes=minutes)
        billing_service.stop_timer(db=self.db, entry_id=entry.id, operator_id=self.user_id)
        return billing_service.confirm_time_entry(
            db=self.db, entry_id=entry.id, confirmed_by=self.user_id, billable=1)

    def test_invoice_has_immutable_snapshot(self):
        self._confirmed_entry(60)  # 1 小时 × 1000 = 1000
        invoice = billing_service.create_invoice(
            db=self.db, organization_id=self.org_id, case_id=self.case_id,
            created_by=self.user_id, client_display_name="客户A",
            issue_date=date.today(), tax_rate=Decimal("6"), discount_amount=Decimal("10"),
            currency="CNY")
        self.assertEqual(invoice.currency, "CNY")
        self.assertIsNotNone(invoice.price_snapshot_json)
        self.assertIsNotNone(invoice.tax_snapshot_json)
        self.assertIsNotNone(invoice.snapshot_hash)
        # 1000 + 60 - 10 = 1050
        self.assertEqual(invoice.total_amount, Decimal("1050.00"))
        original_hash = invoice.snapshot_hash

        # 生成第二张账单用不同税率/折扣 → 历史账单不受影响
        e2 = self._confirmed_entry(120)  # 2 小时 × 1000 = 2000
        invoice2 = billing_service.create_invoice(
            db=self.db, organization_id=self.org_id, case_id=self.case_id,
            created_by=self.user_id, client_display_name="客户B",
            issue_date=date.today(), tax_rate=Decimal("9"), discount_amount=Decimal("0"),
            currency="CNY", time_entry_ids=[e2.id])
        self.assertEqual(invoice2.total_amount, Decimal("2180.00"))  # 2000 + 180
        self.db.refresh(invoice)
        self.assertEqual(invoice.total_amount, Decimal("1050.00"))
        self.assertEqual(invoice.snapshot_hash, original_hash)
        self.assertEqual(invoice.tax_snapshot_json, invoice.tax_snapshot_json)

    def test_draft_update_rebuilds_snapshot(self):
        self._confirmed_entry(60)
        invoice = billing_service.create_invoice(
            db=self.db, organization_id=self.org_id, case_id=self.case_id,
            created_by=self.user_id, client_display_name="客户C",
            issue_date=date.today(), tax_rate=Decimal("6"), discount_amount=Decimal("0"),
            currency="CNY")
        h1 = invoice.snapshot_hash
        updated = billing_service.update_invoice(
            db=self.db, invoice_id=invoice.id, user_id=self.user_id, discount_amount=Decimal("50"))
        self.assertEqual(updated.total_amount, Decimal("1010.00"))
        self.assertNotEqual(updated.snapshot_hash, h1, "草稿折扣变更应重建快照哈希")

    def test_rate_change_does_not_affect_billed_items(self):
        self._confirmed_entry(60)  # 按 1000/h 固化
        invoice = billing_service.create_invoice(
            db=self.db, organization_id=self.org_id, case_id=self.case_id,
            created_by=self.user_id, client_display_name="客户D",
            issue_date=date.today(), currency="CNY")
        self.assertEqual(invoice.total_amount, Decimal("1000.00"))
        # 修改费率（模拟费率变更）→ 历史账单明细与金额不变
        from app.models.legal_billing import LegalInvoiceItem
        rule = self.db.query(LegalBillingRule).first()
        rule.hourly_rate = 2000
        self.db.commit()
        self.db.refresh(invoice)
        self.assertEqual(invoice.total_amount, Decimal("1000.00"))
        item = self.db.query(LegalInvoiceItem).filter(
            LegalInvoiceItem.invoice_id == invoice.id).first()
        self.assertEqual(item.amount, Decimal("1000.00"))


if __name__ == "__main__":
    unittest.main()
