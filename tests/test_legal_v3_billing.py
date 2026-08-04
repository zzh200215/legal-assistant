"""V3.0 回归测试 — 计时计费：状态机、幂等、账单不可变"""
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import hash_password
from app.core.database import Base, get_db
from app.main import app
from app.models.user import User, UserStatus
from app.models.org import Organization, OrganizationMember
from app.models.legal import LegalCase
from app.models.legal_billing import LegalBillingRule, LegalTimeEntry, LegalInvoice, LegalInvoiceItem
from app.services.billing_service import billing_service
from fastapi.testclient import TestClient
from app.core.auth import create_access_token


def _make_engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


class TimeEntryStateMachineTests(unittest.TestCase):
    """计时条目状态机：running/paused/completed/voided + 同一用户只能有一条 running"""

    def setUp(self):
        self.engine = _make_engine()
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session()

        org = Organization(name="TestOrg", code="TORG")
        self.db.add(org)
        self.db.flush()

        user = User(
            username="lawyer1",
            email="lawyer1@t.com",
            hashed_password=hash_password("pw"),
            role="user",
            status=UserStatus.active.value,
            organization_id=org.id,
        )
        self.db.add(user)
        self.db.flush()

        self.db.add(OrganizationMember(
            organization_id=org.id, user_id=user.id, legal_role="reviewer"))
        self.db.flush()

        case = LegalCase(
            title="TestCase",
            case_type="other",
            organization_id=org.id,
            user_id=user.id,
        )
        self.db.add(case)
        self.db.commit()

        self.org_id = org.id
        self.user = user
        self.case = case

        def _override_db():
            yield self.db

        app.dependency_overrides[get_db] = _override_db
        token = create_access_token({"sub": str(user.id)})
        self.client = TestClient(app, raise_server_exceptions=False)
        self.headers = {"Authorization": f"Bearer {token}"}

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    # ── 状态转换 ──────────────────────────────────────────────────────────────

    def _create_running_entry(self):
        entry = LegalTimeEntry(
            organization_id=self.org_id,
            case_id=self.case.id,
            operator_id=self.user.id,
            status="running",
            started_at=datetime.now(timezone.utc),
            description="开庭准备",
        )
        self.db.add(entry)
        self.db.commit()
        self.db.refresh(entry)
        return entry

    def test_valid_pause_from_running(self):
        entry = self._create_running_entry()
        resp = self.client.patch(
            f"/api/legal/time-entries/{entry.id}",
            json={"action": "pause"},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 200)
        self.db.refresh(entry)
        self.assertEqual(entry.status, "paused")

    def test_valid_complete_from_running(self):
        entry = self._create_running_entry()
        resp = self.client.patch(
            f"/api/legal/time-entries/{entry.id}",
            json={"action": "complete"},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 200)
        self.db.refresh(entry)
        self.assertEqual(entry.status, "completed")

    def test_cannot_complete_already_completed(self):
        entry = self._create_running_entry()
        entry.status = "completed"
        self.db.commit()
        resp = self.client.patch(
            f"/api/legal/time-entries/{entry.id}",
            json={"action": "complete"},
            headers=self.headers,
        )
        self.assertIn(resp.status_code, (409, 400))

    def test_only_one_running_per_user(self):
        """同一用户同时只允许一条 running"""
        self._create_running_entry()
        resp = self.client.post(
            f"/api/legal/orgs/{self.org_id}/cases/{self.case.id}/time-entries",
            json={"case_id": self.case.id, "description": "第二条"},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 409)
        data = resp.json()
        self.assertIn("TIME_ENTRY_ALREADY_RUNNING", str(data))

    def test_void_removes_from_billable(self):
        entry = self._create_running_entry()
        entry.status = "completed"
        entry.duration_minutes = 30
        self.db.commit()
        resp = self.client.patch(
            f"/api/legal/time-entries/{entry.id}",
            json={"action": "void"},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 200)
        self.db.refresh(entry)
        self.assertEqual(entry.status, "voided")


class InvoiceImmutabilityTests(unittest.TestCase):
    """账单已付款/已发送后不可修改（INVOICE_IMMUTABLE）"""

    def setUp(self):
        self.engine = _make_engine()
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session()

        org = Organization(name="BillOrg", code="BORG")
        self.db.add(org)
        self.db.flush()

        user = User(
            username="admin1",
            email="admin1@t.com",
            hashed_password=hash_password("pw"),
            role="user",
            status=UserStatus.active.value,
            organization_id=org.id,
        )
        self.db.add(user)
        self.db.flush()
        self.db.add(OrganizationMember(
            organization_id=org.id, user_id=user.id, legal_role="admin"))
        self.db.commit()

        self.org_id = org.id
        self.user = user

        def _override_db():
            yield self.db

        app.dependency_overrides[get_db] = _override_db
        token = create_access_token({"sub": str(user.id)})
        self.client = TestClient(app, raise_server_exceptions=False)
        self.headers = {"Authorization": f"Bearer {token}"}

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def _make_invoice(self, status: str) -> LegalInvoice:
        case = LegalCase(
            title="InvCase",
            case_type="other",
            organization_id=self.org_id,
            user_id=self.user.id,
        )
        self.db.add(case)
        self.db.flush()
        from datetime import date
        inv = LegalInvoice(
            organization_id=self.org_id,
            case_id=case.id,
            invoice_no=f"INV-{self.org_id}-00001-{status}",
            client_display_name="TestClient",
            issue_date=date.today(),
            status=status,
            created_by=self.user.id,
            subtotal=1000,
            total_amount=1000,
        )
        self.db.add(inv)
        self.db.commit()
        self.db.refresh(inv)
        return inv

    def test_cannot_void_paid_invoice(self):
        inv = self._make_invoice("paid")
        resp = self.client.post(
            f"/api/legal/invoices/{inv.id}/void",
            params={"reason": "test void"},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 409)
        data = resp.json()
        self.assertIn("INVOICE_IMMUTABLE", str(data))

    def test_can_void_draft_invoice(self):
        inv = self._make_invoice("draft")
        resp = self.client.post(
            f"/api/legal/invoices/{inv.id}/void",
            params={"reason": "test void"},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 200)

    def test_confirmed_time_entry_is_snapshotted_once(self):
        case = LegalCase(title="BillingCase", case_type="other", organization_id=self.org_id, user_id=self.user.id)
        self.db.add(case)
        self.db.flush()
        entry = LegalTimeEntry(
            organization_id=self.org_id, case_id=case.id, operator_id=self.user.id,
            status="completed", description="庭前准备", duration_minutes=60,
            billable=1, hourly_rate=Decimal("100.00"), billed_amount=Decimal("100.00"),
        )
        self.db.add(entry)
        self.db.commit()

        invoice = billing_service.create_invoice(
            db=self.db, organization_id=self.org_id, case_id=case.id,
            created_by=self.user.id, client_display_name="Client", issue_date=date.today(),
        )
        item = self.db.query(LegalInvoiceItem).filter(LegalInvoiceItem.invoice_id == invoice.id).one()
        self.assertEqual(item.time_entry_id, entry.id)
        self.assertEqual(Decimal(str(invoice.total_amount)), Decimal("100.00"))
        with self.assertRaisesRegex(ValueError, "已出现在其他有效费用通知单"):
            billing_service.create_invoice(
                db=self.db, organization_id=self.org_id, case_id=case.id,
                created_by=self.user.id, client_display_name="Client", issue_date=date.today(),
            )

    def test_payment_and_refund_cannot_exceed_available_balance(self):
        invoice = self._make_invoice("sent")
        payment = billing_service.record_payment(
            db=self.db, invoice_id=invoice.id, organization_id=self.org_id,
            amount=Decimal("600"), payment_method="bank_transfer", recorded_by=self.user.id,
            transaction_id="bank-1",
        )
        with self.assertRaisesRegex(ValueError, "应收余额"):
            billing_service.record_payment(
                db=self.db, invoice_id=invoice.id, organization_id=self.org_id,
                amount=Decimal("401"), payment_method="bank_transfer", recorded_by=self.user.id,
                transaction_id="bank-2",
            )
        refund = billing_service.request_refund(
            db=self.db, invoice_id=invoice.id, organization_id=self.org_id,
            payment_record_id=payment.id, amount=Decimal("500"), reason="更正", recorded_by=self.user.id,
        )
        self.assertEqual(refund.status, "pending")
        with self.assertRaisesRegex(ValueError, "可退余额"):
            billing_service.request_refund(
                db=self.db, invoice_id=invoice.id, organization_id=self.org_id,
                payment_record_id=payment.id, amount=Decimal("101"), reason="重复", recorded_by=self.user.id,
            )

    def test_fixed_stage_fee_is_snapshotted_once(self):
        case = LegalCase(title="FixedCase", case_type="other", organization_id=self.org_id, user_id=self.user.id)
        self.db.add(case)
        self.db.flush()
        self.db.add(LegalBillingRule(
            organization_id=self.org_id, case_id=case.id, name="立案阶段",
            billing_mode="fixed_stage", fixed_amount=Decimal("800.00"), created_by=self.user.id,
        ))
        self.db.commit()
        invoice = billing_service.create_invoice(
            db=self.db, organization_id=self.org_id, case_id=case.id,
            created_by=self.user.id, client_display_name="Client", issue_date=date.today(),
        )
        self.assertEqual(Decimal(str(invoice.total_amount)), Decimal("800.00"))
        with self.assertRaisesRegex(ValueError, "没有可生成"):
            billing_service.create_invoice(
                db=self.db, organization_id=self.org_id, case_id=case.id,
                created_by=self.user.id, client_display_name="Client", issue_date=date.today(),
            )
