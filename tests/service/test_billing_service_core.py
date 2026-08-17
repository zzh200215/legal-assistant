"""Service 层：billing_service 计时/费率/金额/PDF 回退链补测。

覆盖 app/services/billing/billing_service.py：
- start_timer：幂等键去重（同键返回已有）、正常开启；
- stop_timer：不存在/非 running 拒绝、正常停止写入时长；
- confirm_time_entry：重复确认拒绝、可计费固化费率与金额快照、不计费清零；
- _resolve_hourly_rate：entry 规则 → 案件规则 → 组织规则 → ZERO 优先级；
- calculate_amounts：期间过滤与金额明细；
- generate_invoice_pdf：reportlab/fpdf2 ImportError 回退链 + 纯文本回退内容。
"""

import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.core.time import utc_now
from app.models.legal import LegalCase
from app.models.legal_billing import (
    LegalBillingRule,
    LegalInvoice,
    LegalInvoiceItem,
    LegalTimeEntry,
)
from app.services.billing.billing_service import (
    _generate_invoice_pdf_text,
    billing_service,
    generate_invoice_pdf,
)


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


def _make_invoice(db, **kw) -> LegalInvoice:
    fields = {
        "organization_id": 1, "case_id": 1, "invoice_no": "INV-PDF", "client_display_name": "客户甲",
        "issue_date": date(2026, 8, 1), "subtotal": Decimal("100.00"), "tax_amount": Decimal("6.00"),
        "discount_amount": Decimal("0.00"), "total_amount": Decimal("106.00"),
        "status": "draft", "payment_progress": "unpaid", "currency": "CNY", "created_by": 1,
    }
    fields.update(kw)
    invoice = LegalInvoice(**fields)
    db.add(invoice)
    db.commit()
    db.refresh(invoice)
    return invoice


class BillingServiceCoreTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    # ── 计时条目 ────────────────────────────────────────────────────────────
    def test_start_timer_idempotent_by_key(self):
        first = billing_service.start_timer(
            db=self.db, organization_id=1, case_id=1, operator_id=2,
            description="起草诉状", idempotency_key="timer:abc",
        )
        second = billing_service.start_timer(
            db=self.db, organization_id=1, case_id=1, operator_id=2,
            description="起草诉状", idempotency_key="timer:abc",
        )
        self.assertEqual(first.id, second.id)  # 同键返回已有条目
        self.assertEqual(self.db.query(LegalTimeEntry).count(), 1)

    def test_start_timer_new_entry(self):
        entry = billing_service.start_timer(
            db=self.db, organization_id=1, case_id=1, operator_id=2, description="审查合同",
        )
        self.assertEqual(entry.status, "running")
        self.assertEqual(entry.billable, 0)

    def test_stop_timer_errors(self):
        with self.assertRaises(ValueError):
            billing_service.stop_timer(db=self.db, entry_id=999, operator_id=2)
        entry = billing_service.start_timer(
            db=self.db, organization_id=1, case_id=1, operator_id=2, description="x",
        )
        billing_service.stop_timer(db=self.db, entry_id=entry.id, operator_id=2)
        with self.assertRaises(ValueError):
            billing_service.stop_timer(db=self.db, entry_id=entry.id, operator_id=2)  # 非 running

    def test_stop_timer_writes_duration(self):
        entry = billing_service.start_timer(
            db=self.db, organization_id=1, case_id=1, operator_id=2, description="x",
        )
        entry.started_at = utc_now()
        self.db.commit()
        stopped = billing_service.stop_timer(db=self.db, entry_id=entry.id, operator_id=2)
        self.assertEqual(stopped.status, "completed")
        self.assertIsNotNone(stopped.duration_minutes)

    # ── 确认计费 ────────────────────────────────────────────────────────────
    def _rule(self, *, case_id=None, rate="500.00") -> LegalBillingRule:
        rule = LegalBillingRule(
            organization_id=1, case_id=case_id, name="费率", billing_mode="hourly",
            hourly_rate=Decimal(rate), currency="CNY", is_active=1, created_by=1,
        )
        self.db.add(rule)
        self.db.commit()
        self.db.refresh(rule)
        return rule

    def test_confirm_time_entry_errors(self):
        with self.assertRaises(ValueError):
            billing_service.confirm_time_entry(db=self.db, entry_id=999, confirmed_by=1)
        entry = billing_service.start_timer(
            db=self.db, organization_id=1, case_id=1, operator_id=2, description="x",
        )
        billing_service.confirm_time_entry(db=self.db, entry_id=entry.id, confirmed_by=1, billable=1)
        with self.assertRaises(ValueError):
            billing_service.confirm_time_entry(db=self.db, entry_id=entry.id, confirmed_by=1)  # 重复确认

    def test_confirm_time_entry_computes_amount(self):
        entry = billing_service.start_timer(
            db=self.db, organization_id=1, case_id=1, operator_id=2, description="x",
        )
        entry.started_at = utc_now()
        entry.duration_minutes = 60
        self.db.commit()
        self._rule(case_id=1, rate="500.00")
        confirmed = billing_service.confirm_time_entry(
            db=self.db, entry_id=entry.id, confirmed_by=1, billable=1)
        self.assertEqual(confirmed.hourly_rate, Decimal("500.00"))
        self.assertEqual(confirmed.billed_amount, Decimal("500.00"))  # 60 分钟 × 500/小时

    def test_confirm_time_entry_non_billable_zeroes_amount(self):
        entry = billing_service.start_timer(
            db=self.db, organization_id=1, case_id=1, operator_id=2, description="x",
        )
        confirmed = billing_service.confirm_time_entry(
            db=self.db, entry_id=entry.id, confirmed_by=1, billable=2)
        self.assertEqual(confirmed.billable, 2)
        self.assertIsNone(confirmed.billed_amount)

    # ── 费率解析优先级 ──────────────────────────────────────────────────────
    def test_resolve_hourly_rate_precedence(self):
        entry = billing_service.start_timer(
            db=self.db, organization_id=1, case_id=1, operator_id=2, description="x",
        )
        org_rule = self._rule(case_id=None, rate="300.00")  # 组织默认
        entry.billing_rule_id = org_rule.id
        self.db.commit()
        self.assertEqual(billing_service._resolve_hourly_rate(self.db, entry), Decimal("300.00"))
        # 案件规则优先于组织规则
        case_rule = self._rule(case_id=1, rate="500.00")
        entry.billing_rule_id = None
        self.db.commit()
        self.assertEqual(billing_service._resolve_hourly_rate(self.db, entry), Decimal("500.00"))
        # entry 显式规则优先
        entry_rule = self._rule(case_id=None, rate="800.00")
        entry.billing_rule_id = entry_rule.id
        self.db.commit()
        self.assertEqual(billing_service._resolve_hourly_rate(self.db, entry), Decimal("800.00"))
        # 无可用规则 → ZERO（禁用全部）
        entry.billing_rule_id = None
        case_rule.is_active = 0
        org_rule.is_active = 0
        entry_rule.is_active = 0
        self.db.commit()
        self.assertEqual(billing_service._resolve_hourly_rate(self.db, entry), Decimal("0.00"))

    # ── 金额计算 ────────────────────────────────────────────────────────────
    def test_calculate_amounts_filters_period(self):
        e1 = billing_service.start_timer(
            db=self.db, organization_id=1, case_id=1, operator_id=2, description="早",
        )
        e1.billable = 1
        e1.billed_amount = Decimal("100.00")
        e1.started_at = utc_now()
        self.db.commit()
        rows = billing_service.calculate_amounts(
            db=self.db, organization_id=1, case_id=1,
            period_start=date(2026, 8, 1), period_end=date(2026, 8, 31),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["billed_amount"], "100.00")

    # ── 发票创建 ────────────────────────────────────────────────────────────
    def _confirmed_entry(self, *, minutes=60, amount="500.00") -> LegalTimeEntry:
        entry = billing_service.start_timer(
            db=self.db, organization_id=1, case_id=1, operator_id=2, description="起草起诉状",
        )
        entry.started_at = utc_now()
        entry.status = "completed"
        entry.billable = 1
        entry.duration_minutes = minutes
        entry.billed_amount = Decimal(amount)
        self.db.commit()
        self.db.refresh(entry)
        return entry

    def test_create_invoice_idempotent_by_key(self):
        entry = self._confirmed_entry()
        first = billing_service.create_invoice(
            db=self.db, organization_id=1, case_id=1, created_by=1,
            client_display_name="客户甲", issue_date=date(2026, 8, 1),
            time_entry_ids=[entry.id], idempotency_key="inv:abc",
        )
        second = billing_service.create_invoice(
            db=self.db, organization_id=1, case_id=1, created_by=1,
            client_display_name="客户甲", issue_date=date(2026, 8, 1),
            time_entry_ids=[entry.id], idempotency_key="inv:abc",
        )
        self.assertEqual(first.id, second.id)

    def test_create_invoice_no_entries_raises(self):
        with self.assertRaises(ValueError):
            billing_service.create_invoice(
                db=self.db, organization_id=1, case_id=1, created_by=1,
                client_display_name="客户甲", issue_date=date(2026, 8, 1),
            )

    def test_create_invoice_unknown_entry_raises(self):
        with self.assertRaises(ValueError):
            billing_service.create_invoice(
                db=self.db, organization_id=1, case_id=1, created_by=1,
                client_display_name="客户甲", issue_date=date(2026, 8, 1),
                time_entry_ids=[9999],
            )

    def test_create_invoice_success_with_snapshot(self):
        entry = self._confirmed_entry(minutes=120, amount="1000.00")
        invoice = billing_service.create_invoice(
            db=self.db, organization_id=1, case_id=1, created_by=1,
            client_display_name="客户甲", issue_date=date(2026, 8, 1),
            time_entry_ids=[entry.id], tax_rate=Decimal("6.00"),
        )
        self.assertEqual(invoice.status, "draft")
        self.assertEqual(invoice.subtotal, Decimal("1000.00"))
        self.assertEqual(invoice.tax_amount, Decimal("60.00"))
        self.assertEqual(invoice.total_amount, Decimal("1060.00"))
        self.assertIsNotNone(invoice.snapshot_hash)
        # 明细快照已固化
        items = self.db.query(LegalInvoiceItem).filter(LegalInvoiceItem.invoice_id == invoice.id).all()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].time_entry_id, entry.id)

    def test_create_invoice_rejects_double_billing(self):
        entry = self._confirmed_entry()
        billing_service.create_invoice(
            db=self.db, organization_id=1, case_id=1, created_by=1,
            client_display_name="客户甲", issue_date=date(2026, 8, 1),
            time_entry_ids=[entry.id],
        )
        with self.assertRaises(ValueError):
            billing_service.create_invoice(
                db=self.db, organization_id=1, case_id=1, created_by=1,
                client_display_name="客户甲", issue_date=date(2026, 8, 1),
                time_entry_ids=[entry.id],
            )  # 已出账条目不可重复

    # ── PDF 回退链 ──────────────────────────────────────────────────────────
    def test_pdf_text_fallback_content(self):
        invoice = _make_invoice(self.db)
        case = LegalCase(title="张三劳动争议案", case_type="labor", organization_id=1, user_id=1)
        self.db.add(case)
        self.db.commit()
        self.db.refresh(case)
        self.db.add(LegalInvoiceItem(
            invoice_id=invoice.id, title="起草起诉状", duration_minutes=120,
            unit_price=Decimal("500.00"), quantity=Decimal("1.00"), amount=Decimal("1000.00"),
        ))
        self.db.commit()
        items = self.db.query(LegalInvoiceItem).filter(LegalInvoiceItem.invoice_id == invoice.id).all()
        payload = _generate_invoice_pdf_text(invoice, items, case, org_name="律智检律所")
        text = payload.decode("utf-8")
        self.assertIn("账单编号：INV-PDF", text)
        self.assertIn("客户名称：客户甲", text)
        self.assertIn("案件名称：张三劳动争议案", text)
        self.assertIn("合计：106.00", text)
        self.assertIn("起草起诉状", text)

    def test_generate_pdf_falls_back_through_import_errors(self):
        invoice = _make_invoice(self.db)
        with (
            patch("app.services.billing.billing_service._generate_pdf_reportlab",
                  side_effect=ImportError("no reportlab")),
            patch("app.services.billing.billing_service._generate_pdf_fpdf2",
                  side_effect=ImportError("no fpdf2")),
        ):
            payload = generate_invoice_pdf(invoice, [], None)
        self.assertIn(b"INV-PDF", payload)  # 纯文本回退

    def test_generate_pdf_reportlab_runtime_error_falls_back(self):
        invoice = _make_invoice(self.db)
        with (
            patch("app.services.billing.billing_service._generate_pdf_reportlab",
                  side_effect=RuntimeError("reportlab broke")),
            patch("app.services.billing.billing_service._generate_pdf_fpdf2",
                  side_effect=ImportError("no fpdf2")),
        ):
            payload = generate_invoice_pdf(invoice, [], None)
        self.assertIn(b"INV-PDF", payload)

    def test_generate_pdf_fpdf2_success(self):
        invoice = _make_invoice(self.db)
        with (
            patch("app.services.billing.billing_service._generate_pdf_reportlab",
                  side_effect=ImportError("no reportlab")),
            patch("app.services.billing.billing_service._generate_pdf_fpdf2", return_value=b"FPDF-BYTES"),
        ):
            payload = generate_invoice_pdf(invoice, [], None)
        self.assertEqual(payload, b"FPDF-BYTES")


if __name__ == "__main__":
    unittest.main()
