"""Phase 11 — 计时计费服务

处理账单全生命周期：PDF 生成、金额计算、邮件发送、支付/退款处理。
"""
from __future__ import annotations

import io
import json
import logging
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence

from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.legal import LegalCase
from app.models.legal_billing import (
    LegalBillingRule,
    LegalCollectionReminder,
    LegalInvoice,
    LegalInvoiceItem,
    LegalPaymentRecord,
    LegalRefundRecord,
    LegalTimeEntry,
)
from app.models.user import User
from app.services.oplog_service import oplog_service

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────

INVOICE_STATES = ("draft", "sent", "paid", "overdue", "voided")
PAYMENT_PROGRESS_STATES = ("unpaid", "partial_paid", "fully_paid", "refunding", "refunded")
ZERO = Decimal("0.00")
TWO_PLACES = Decimal("0.01")


def _d(value) -> Decimal:
    """安全转为 Decimal，None 视为 0。"""
    if value is None:
        return ZERO
    return Decimal(str(value)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


# ── PDF 生成 ──────────────────────────────────────────────────────

def _generate_invoice_pdf_text(invoice: LegalInvoice, items: Sequence[LegalInvoiceItem],
                               case: LegalCase | None, org_name: str = "") -> bytes:
    """纯文本回退：当 reportlab / fpdf2 均不可用时生成文本格式账单。"""
    lines = [
        "=" * 60,
        f"  账单编号：{invoice.invoice_no}",
        f"  客户名称：{invoice.client_display_name}",
        f"  案件名称：{case.title if case else '-'}",
        f"  开票日期：{invoice.issue_date}",
        f"  到期日期：{invoice.due_date or '-'}",
        f"  计费区间：{invoice.billing_period_start or '-'} ~ {invoice.billing_period_end or '-'}",
        "-" * 60,
        f"  {'序号':<4}{'项目':<30}{'时长(分钟)':<12}{'单价':<12}{'金额':<14}",
        "-" * 60,
    ]
    for idx, item in enumerate(items, 1):
        duration = str(item.duration_minutes or "-")
        unit_price = str(item.unit_price or "0.00")
        amount = str(item.amount or "0.00")
        lines.append(f"  {idx:<4}{item.title:<30}{duration:<12}{unit_price:<12}{amount:<14}")
    lines.extend([
        "-" * 60,
        f"  小计：{invoice.subtotal}",
        f"  税额：{invoice.tax_amount}",
        f"  折扣：{invoice.discount_amount}",
        "=" * 60,
        f"  合计：{invoice.total_amount}",
        "=" * 60,
    ])
    return "\n".join(lines).encode("utf-8")


def generate_invoice_pdf(invoice: LegalInvoice, items: Sequence[LegalInvoiceItem],
                         case: LegalCase | None, org_name: str = "",
                         logo_path: str | None = None) -> bytes:
    """生成账单 PDF 字节流。优先使用 reportlab，其次 fpdf2，最后回退纯文本。"""
    # 尝试 reportlab
    try:
        return _generate_pdf_reportlab(invoice, items, case, org_name, logo_path)
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("reportlab PDF 生成失败，回退: %s", exc)

    # 尝试 fpdf2
    try:
        return _generate_pdf_fpdf2(invoice, items, case, org_name, logo_path)
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("fpdf2 PDF 生成失败，回退: %s", exc)

    # 纯文本回退
    return _generate_invoice_pdf_text(invoice, items, case, org_name)


def _generate_pdf_reportlab(invoice: LegalInvoice, items: Sequence[LegalInvoiceItem],
                            case: LegalCase | None, org_name: str,
                            logo_path: str | None) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
    from reportlab.lib.styles import getSampleStyleSheet

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=20 * mm, bottomMargin=20 * mm)
    styles = getSampleStyleSheet()
    elements = []

    # Logo
    if logo_path:
        try:
            img = Image(logo_path, width=60 * mm, height=20 * mm)
            elements.append(img)
            elements.append(Spacer(1, 5 * mm))
        except Exception:
            pass

    # 标题
    elements.append(Paragraph(f"账单 {invoice.invoice_no}", styles["Title"]))
    elements.append(Spacer(1, 4 * mm))

    # 基本信息
    info_data = [
        ["客户名称", invoice.client_display_name],
        ["案件", case.title if case else "-"],
        ["开票日期", str(invoice.issue_date)],
        ["到期日期", str(invoice.due_date or "-")],
        ["计费区间", f"{invoice.billing_period_start or '-'} ~ {invoice.billing_period_end or '-'}"],
    ]
    if org_name:
        info_data.insert(0, ["律所", org_name])
    info_table = Table(info_data, colWidths=[80, 300])
    info_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 6 * mm))

    # 明细表
    table_data = [["序号", "项目", "时长(分钟)", "单价", "金额"]]
    for idx, item in enumerate(items, 1):
        table_data.append([
            str(idx),
            item.title,
            str(item.duration_minutes or "-"),
            str(item.unit_price),
            str(item.amount),
        ])
    table_data.append(["", "", "", "小计", str(invoice.subtotal)])
    table_data.append(["", "", "", "税额", str(invoice.tax_amount)])
    table_data.append(["", "", "", "折扣", str(invoice.discount_amount)])
    table_data.append(["", "", "", "合计", str(invoice.total_amount)])

    col_widths = [30, 200, 70, 70, 80]
    detail_table = Table(table_data, colWidths=col_widths)
    detail_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.2, 0.2, 0.5)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("LINEABOVE", (0, -1), (-1, -1), 1.5, colors.black),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
    ]))
    elements.append(detail_table)

    doc.build(elements)
    return buf.getvalue()


def _generate_pdf_fpdf2(invoice: LegalInvoice, items: Sequence[LegalInvoiceItem],
                        case: LegalCase | None, org_name: str,
                        logo_path: str | None) -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, f"Invoice {invoice.invoice_no}", ln=True, align="C")
    pdf.ln(5)

    if org_name:
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 6, f"Firm: {org_name}", ln=True)

    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Client: {invoice.client_display_name}", ln=True)
    pdf.cell(0, 6, f"Case: {case.title if case else '-'}", ln=True)
    pdf.cell(0, 6, f"Issue Date: {invoice.issue_date}", ln=True)
    pdf.cell(0, 6, f"Due Date: {invoice.due_date or '-'}", ln=True)
    pdf.ln(5)

    # 表头
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(10, 7, "#", border=1)
    pdf.cell(80, 7, "Item", border=1)
    pdf.cell(30, 7, "Min", border=1, align="R")
    pdf.cell(30, 7, "Unit", border=1, align="R")
    pdf.cell(35, 7, "Amount", border=1, align="R")
    pdf.ln()

    pdf.set_font("Helvetica", "", 9)
    for idx, item in enumerate(items, 1):
        pdf.cell(10, 6, str(idx), border=1)
        pdf.cell(80, 6, item.title[:40], border=1)
        pdf.cell(30, 6, str(item.duration_minutes or "-"), border=1, align="R")
        pdf.cell(30, 6, str(item.unit_price), border=1, align="R")
        pdf.cell(35, 6, str(item.amount), border=1, align="R")
        pdf.ln()

    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(150, 7, "Subtotal", border=1, align="R")
    pdf.cell(35, 7, str(invoice.subtotal), border=1, align="R")
    pdf.ln()
    pdf.cell(150, 7, "Tax", border=1, align="R")
    pdf.cell(35, 7, str(invoice.tax_amount), border=1, align="R")
    pdf.ln()
    pdf.cell(150, 7, "Discount", border=1, align="R")
    pdf.cell(35, 7, str(invoice.discount_amount), border=1, align="R")
    pdf.ln()
    pdf.cell(150, 7, "Total", border=1, align="R")
    pdf.cell(35, 7, str(invoice.total_amount), border=1, align="R")

    return pdf.output()


# ── 主服务类 ──────────────────────────────────────────────────────

class BillingService:

    # ── 计时条目 ──────────────────────────────────────────────────

    def start_timer(self, *, db: Session, organization_id: int, case_id: int,
                    operator_id: int, description: str = "",
                    billing_rule_id: int | None = None,
                    idempotency_key: str | None = None) -> LegalTimeEntry:
        """开始实时计时。"""
        if idempotency_key:
            existing = db.query(LegalTimeEntry).filter(
                LegalTimeEntry.idempotency_key == idempotency_key
            ).first()
            if existing:
                return existing

        now = datetime.now(timezone.utc)
        entry = LegalTimeEntry(
            organization_id=organization_id,
            case_id=case_id,
            operator_id=operator_id,
            billing_rule_id=billing_rule_id,
            started_at=now,
            status="running",
            description=description,
            billable=0,
            idempotency_key=idempotency_key or uuid.uuid4().hex,
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return entry

    def stop_timer(self, *, db: Session, entry_id: int, operator_id: int) -> LegalTimeEntry:
        """停止计时并写入时长。"""
        entry = db.query(LegalTimeEntry).filter(
            LegalTimeEntry.id == entry_id,
            LegalTimeEntry.operator_id == operator_id,
        ).first()
        if not entry:
            raise ValueError("计时条目不存在")
        if entry.status != "running":
            raise ValueError(f"计时条目状态为 {entry.status}，无法停止")

        now = datetime.now(timezone.utc)
        entry.ended_at = now
        if entry.started_at:
            delta = now - entry.started_at
            entry.duration_minutes = int(delta.total_seconds() / 60)
        entry.status = "completed"
        db.commit()
        db.refresh(entry)
        return entry

    def confirm_time_entry(self, *, db: Session, entry_id: int, confirmed_by: int,
                           billable: int = 1) -> LegalTimeEntry:
        """确认计时条目为可计费/不计费，固化费率和金额快照。"""
        entry = db.query(LegalTimeEntry).filter(LegalTimeEntry.id == entry_id).first()
        if not entry:
            raise ValueError("计时条目不存在")
        if entry.billable != 0:
            raise ValueError("该条目已确认，不可重复确认")

        entry.billable = billable
        entry.confirmed_by = confirmed_by
        entry.confirmed_at = datetime.now(timezone.utc)

        if billable == 1:
            rate = self._resolve_hourly_rate(db, entry)
            entry.hourly_rate = rate
            if entry.duration_minutes and rate:
                minutes = Decimal(str(entry.duration_minutes))
                entry.billed_amount = (minutes / Decimal("60") * rate).quantize(
                    TWO_PLACES, rounding=ROUND_HALF_UP
                )
            else:
                entry.billed_amount = ZERO

        db.commit()
        db.refresh(entry)
        return entry

    def _resolve_hourly_rate(self, db: Session, entry: LegalTimeEntry) -> Decimal:
        """解析计时条目适用的小时费率：案件规则优先，否则组织默认规则。"""
        if entry.billing_rule_id:
            rule = db.query(LegalBillingRule).filter(LegalBillingRule.id == entry.billing_rule_id).first()
            if rule and rule.is_active:
                return _d(rule.hourly_rate)

        # 查找案件专属规则
        case_rule = db.query(LegalBillingRule).filter(
            LegalBillingRule.case_id == entry.case_id,
            LegalBillingRule.organization_id == entry.organization_id,
            LegalBillingRule.is_active == 1,
        ).order_by(LegalBillingRule.created_at.desc()).first()
        if case_rule and case_rule.hourly_rate:
            return _d(case_rule.hourly_rate)

        # 组织默认规则
        org_rule = db.query(LegalBillingRule).filter(
            LegalBillingRule.organization_id == entry.organization_id,
            LegalBillingRule.case_id.is_(None),
            LegalBillingRule.is_active == 1,
        ).order_by(LegalBillingRule.created_at.desc()).first()
        if org_rule and org_rule.hourly_rate:
            return _d(org_rule.hourly_rate)

        return ZERO

    def calculate_amounts(self, *, db: Session, organization_id: int,
                          case_id: int, period_start: date | None = None,
                          period_end: date | None = None) -> list[dict]:
        """计算指定案件/时间段内已确认计费条目的金额明细。"""
        query = db.query(LegalTimeEntry).filter(
            LegalTimeEntry.organization_id == organization_id,
            LegalTimeEntry.case_id == case_id,
            LegalTimeEntry.billable == 1,
            LegalTimeEntry.billed_amount.isnot(None),
        )
        if period_start:
            query = query.filter(LegalTimeEntry.started_at >= period_start)
        if period_end:
            query = query.filter(LegalTimeEntry.started_at < period_end)

        entries = query.order_by(LegalTimeEntry.started_at).all()
        return [
            {
                "time_entry_id": e.id,
                "operator_id": e.operator_id,
                "description": e.description,
                "duration_minutes": e.duration_minutes,
                "hourly_rate": str(e.hourly_rate),
                "billed_amount": str(e.billed_amount),
            }
            for e in entries
        ]

    # ── 账单管理 ──────────────────────────────────────────────────

    def create_invoice(self, *, db: Session, organization_id: int, case_id: int,
                       created_by: int, client_display_name: str,
                       issue_date: date, due_date: date | None = None,
                       billing_period_start: date | None = None,
                       billing_period_end: date | None = None,
                       discount_amount: Decimal = ZERO,
                       tax_rate: Decimal = ZERO,
                       time_entry_ids: list[int] | None = None,
                       idempotency_key: str | None = None) -> LegalInvoice:
        """创建账单：从已确认计时条目自动生成明细行，计算小计/税额/合计。"""
        if idempotency_key:
            existing = db.query(LegalInvoice).filter(
                LegalInvoice.idempotency_key == idempotency_key
            ).first()
            if existing:
                return existing

        # 生成账单编号
        today_str = date.today().strftime("%Y%m%d")
        seq = db.query(sa_func.count(LegalInvoice.id)).filter(
            LegalInvoice.organization_id == organization_id,
            sa_func.date(LegalInvoice.created_at) == date.today(),
        ).scalar() or 0
        invoice_no = f"INV-{today_str}-{organization_id:04d}-{seq + 1:04d}"

        # 仅将已确认且尚未出账的条目写入不可变账单快照。未传 ID 时按账期
        # 自动选择，传入 ID 时必须全部属于该案件且仍可计费，避免静默漏项。
        billed_entry_ids = {
            row[0] for row in db.query(LegalInvoiceItem.time_entry_id).join(
                LegalInvoice, LegalInvoice.id == LegalInvoiceItem.invoice_id
            ).filter(
                LegalInvoice.organization_id == organization_id,
                LegalInvoiceItem.time_entry_id.isnot(None),
                LegalInvoice.status != "voided",
            ).all()
        }
        entry_query = db.query(LegalTimeEntry).filter(
            LegalTimeEntry.organization_id == organization_id,
            LegalTimeEntry.case_id == case_id,
            LegalTimeEntry.billable == 1,
            LegalTimeEntry.status == "completed",
            LegalTimeEntry.billed_amount.isnot(None),
        )
        if billing_period_start:
            entry_query = entry_query.filter(LegalTimeEntry.started_at >= billing_period_start)
        if billing_period_end:
            entry_query = entry_query.filter(LegalTimeEntry.started_at < billing_period_end)
        if time_entry_ids is not None:
            requested_ids = set(time_entry_ids)
            if not requested_ids:
                raise ValueError("至少需要一个已确认可计费条目")
            entries = entry_query.filter(LegalTimeEntry.id.in_(requested_ids)).all()
            if {entry.id for entry in entries} != requested_ids:
                raise ValueError("包含不存在、未确认或不属于该案件的计费条目")
        else:
            entries = entry_query.all()
        duplicate_ids = billed_entry_ids.intersection(entry.id for entry in entries)
        if duplicate_ids:
            raise ValueError("计费条目已出现在其他有效费用通知单中")
        fixed_rules = db.query(LegalBillingRule).filter(
            LegalBillingRule.organization_id == organization_id,
            LegalBillingRule.is_active == 1,
            LegalBillingRule.billing_mode.in_(("fixed_stage", "hybrid")),
            LegalBillingRule.fixed_amount.isnot(None),
            LegalBillingRule.fixed_amount > 0,
            (LegalBillingRule.case_id == case_id) | LegalBillingRule.case_id.is_(None),
        ).all()
        fixed_rules = [
            rule for rule in fixed_rules
            if not rule.effective_from or issue_date >= rule.effective_from
        ]
        fixed_rules = [
            rule for rule in fixed_rules
            if not rule.effective_to or issue_date <= rule.effective_to
        ]
        used_fixed_rule_ids = {
            self._fixed_rule_id(item.description)
            for item in db.query(LegalInvoiceItem).join(
                LegalInvoice, LegalInvoice.id == LegalInvoiceItem.invoice_id
            ).filter(
                LegalInvoice.organization_id == organization_id,
                LegalInvoice.status != "voided",
            ).all()
        }
        fixed_rules = [rule for rule in fixed_rules if rule.id not in used_fixed_rule_ids]
        if not entries and not fixed_rules:
            raise ValueError("没有可生成费用通知单的已确认条目")

        # 计算小计
        subtotal = ZERO
        for e in entries:
            subtotal += _d(e.billed_amount)
        for rule in fixed_rules:
            subtotal += _d(rule.fixed_amount)

        tax_amount = (subtotal * _d(tax_rate) / Decimal("100")).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        total = subtotal + tax_amount - _d(discount_amount)
        if total < ZERO:
            total = ZERO

        invoice = LegalInvoice(
            organization_id=organization_id,
            case_id=case_id,
            invoice_no=invoice_no,
            client_display_name=client_display_name,
            issue_date=issue_date,
            due_date=due_date,
            billing_period_start=billing_period_start,
            billing_period_end=billing_period_end,
            subtotal=subtotal,
            tax_amount=tax_amount,
            discount_amount=_d(discount_amount),
            total_amount=total,
            status="draft",
            payment_progress="unpaid",
            idempotency_key=idempotency_key or uuid.uuid4().hex,
            created_by=created_by,
        )
        db.add(invoice)
        db.flush()

        # 创建明细行
        for idx, e in enumerate(entries):
            db.add(LegalInvoiceItem(
                invoice_id=invoice.id,
                time_entry_id=e.id,
                title=e.description or f"计时 #{e.id}",
                duration_minutes=e.duration_minutes,
                unit_price=_d(e.hourly_rate),
                quantity=Decimal(str(e.duration_minutes or 0)) / Decimal("60") if e.duration_minutes else Decimal("1"),
                discount_rate=ZERO,
                amount=_d(e.billed_amount),
            ))
        for rule in fixed_rules:
            db.add(LegalInvoiceItem(
                invoice_id=invoice.id,
                title=rule.name,
                description=json.dumps({"billing_rule_id": rule.id, "item_type": "fixed_stage"}),
                unit_price=_d(rule.fixed_amount),
                quantity=Decimal("1"),
                discount_rate=ZERO,
                amount=_d(rule.fixed_amount),
            ))

        db.commit()
        db.refresh(invoice)
        oplog_service.log(module="billing", action="invoice_created", db=db,
                          user_id=created_by, target_type="invoice",
                          target_id=invoice.id,
                          detail=f"invoice_no={invoice_no}; subtotal={subtotal}")
        return invoice

    @staticmethod
    def _fixed_rule_id(description: str | None) -> int | None:
        try:
            value = json.loads(description or "{}")
            return int(value["billing_rule_id"]) if value.get("item_type") == "fixed_stage" else None
        except (TypeError, ValueError, KeyError):
            return None

    def _get_invoice_or_raise(self, db: Session, invoice_id: int) -> LegalInvoice:
        invoice = db.query(LegalInvoice).filter(LegalInvoice.id == invoice_id).first()
        if not invoice:
            raise ValueError("账单不存在")
        return invoice

    def _assert_mutable(self, invoice: LegalInvoice) -> None:
        """已付款的账单不可变。"""
        if invoice.status in ("sent", "paid") or invoice.payment_progress in ("fully_paid", "refunding", "refunded"):
            raise ValueError("已发送或已付款费用通知单不可修改")

    def update_invoice(self, *, db: Session, invoice_id: int, user_id: int,
                       **fields) -> LegalInvoice:
        """更新草稿账单字段（discount_amount / tax 等）。"""
        invoice = self._get_invoice_or_raise(db, invoice_id)
        self._assert_mutable(invoice)
        if invoice.status not in ("draft",):
            raise ValueError("仅草稿状态可编辑")

        if "discount_amount" in fields:
            invoice.discount_amount = _d(fields["discount_amount"])
        if "due_date" in fields:
            invoice.due_date = fields["due_date"]

        # 重算合计
        subtotal = _d(invoice.subtotal)
        tax = _d(invoice.tax_amount)
        discount = _d(invoice.discount_amount)
        invoice.total_amount = max(subtotal + tax - discount, ZERO)

        db.commit()
        db.refresh(invoice)
        return invoice

    def generate_and_attach_pdf(self, *, db: Session, invoice_id: int,
                                logo_path: str | None = None) -> LegalInvoice:
        """生成 PDF 并保存路径到 invoice.pdf_path。"""
        invoice = self._get_invoice_or_raise(db, invoice_id)
        items = db.query(LegalInvoiceItem).filter(
            LegalInvoiceItem.invoice_id == invoice_id
        ).order_by(LegalInvoiceItem.id).all()
        case = db.query(LegalCase).filter(LegalCase.id == invoice.case_id).first()

        pdf_bytes = generate_invoice_pdf(invoice, items, case, logo_path=logo_path)

        # 保存到本地文件
        upload_dir = __import__("pathlib").Path("uploads") / "invoices"
        upload_dir.mkdir(parents=True, exist_ok=True)
        file_path = upload_dir / f"{invoice.invoice_no}.pdf"
        file_path.write_bytes(pdf_bytes)

        invoice.pdf_path = str(file_path)
        db.commit()
        db.refresh(invoice)
        return invoice

    def void_invoice(self, *, db: Session, invoice_id: int, user_id: int,
                     reason: str = "") -> LegalInvoice:
        """作废账单。"""
        invoice = self._get_invoice_or_raise(db, invoice_id)
        if invoice.status == "voided":
            return invoice
        if invoice.status == "paid":
            raise ValueError("已付款账单不可作废")
        if invoice.payment_progress in ("partial_paid", "fully_paid"):
            raise ValueError("已有付款记录的账单不可作废，请先退款")

        invoice.status = "voided"
        invoice.voided_at = datetime.now(timezone.utc)
        invoice.void_reason = reason
        db.commit()
        db.refresh(invoice)
        oplog_service.log(module="billing", action="invoice_voided", db=db,
                          user_id=user_id, target_type="invoice",
                          target_id=invoice.id, detail=f"reason={reason}")
        return invoice

    # ── 发送账单 ──────────────────────────────────────────────────

    def send_invoice(self, *, db: Session, invoice_id: int, user_id: int,
                     recipient_email: str | None = None) -> LegalInvoice:
        """创建费用通知单外发申请；SMTP 成功后由外发服务回写 sent 状态。"""
        invoice = self._get_invoice_or_raise(db, invoice_id)
        if invoice.status not in ("draft",):
            raise ValueError("仅草稿状态可发送")

        # 生成 PDF
        self.generate_and_attach_pdf(db=db, invoice_id=invoice_id)

        target_email = recipient_email or getattr(invoice, "client_contact", None)
        if not target_email:
            raise ValueError("费用通知单缺少客户收件邮箱")
        self._send_invoice_email(db=db, invoice=invoice, recipient=target_email, user_id=user_id)
        oplog_service.log(module="billing", action="invoice_send_requested", db=db,
                          user_id=user_id, target_type="invoice",
                          target_id=invoice.id,
                          detail=f"invoice_no={invoice.invoice_no}")
        return invoice

    def _send_invoice_email(self, *, db: Session, invoice: LegalInvoice,
                            recipient: str, user_id: int) -> None:
        """通过 OutboundEmailService 发送账单邮件。"""
        from app.models.email import EmailDraft

        draft = EmailDraft(
            user_id=user_id,
            organization_id=invoice.organization_id,
            subject=f"费用通知单 {invoice.invoice_no}",
            recipient=recipient,
            content=(
                f"尊敬的客户，\n\n"
                f"以下是您的费用通知单：\n"
                f"费用通知单编号：{invoice.invoice_no}\n"
                f"金额：{invoice.total_amount}\n"
                f"到期日：{invoice.due_date or '无'}\n\n"
                f"请及时安排付款，谢谢。"
            ),
            purpose="费用通知单发送（非税务发票）",
            status="draft",
            generation_type="invoice_send",
        )
        draft.metadata_json = json.dumps({"billing_invoice_id": invoice.id}, ensure_ascii=False)
        db.add(draft)
        db.commit()

    # ── 支付处理 ──────────────────────────────────────────────────

    def record_payment(self, *, db: Session, invoice_id: int, organization_id: int,
                       amount: Decimal, payment_method: str,
                       recorded_by: int, transaction_id: str | None = None,
                       provider: str | None = None, note: str | None = None,
                       idempotency_key: str | None = None) -> LegalPaymentRecord:
        """记录一笔付款，幂等处理。"""
        invoice = self._get_invoice_or_raise(db, invoice_id)
        if invoice.status == "voided":
            raise ValueError("已作废账单不可收款")

        amount = _d(amount)
        if amount <= ZERO:
            raise ValueError("收款金额必须大于零")
        # 人工登记与回调以 provider + transaction_id 共用去重空间；未带
        # provider 的人工流水按 transaction_id 去重，防止回调乱序重复入账。
        if transaction_id:
            dup = db.query(LegalPaymentRecord).filter(
                LegalPaymentRecord.transaction_id == transaction_id,
                LegalPaymentRecord.status == "confirmed",
            ).first()
            if dup:
                return dup

        paid_total = self._net_paid_total(db, invoice_id)
        if paid_total + amount > _d(invoice.total_amount):
            raise ValueError("收款金额超过费用通知单应收余额")

        payment = LegalPaymentRecord(
            invoice_id=invoice_id,
            organization_id=organization_id,
            amount=amount,
            payment_method=payment_method,
            transaction_id=transaction_id,
            provider=provider,
            note=note,
            status="confirmed",
            recorded_by=recorded_by,
        )
        db.add(payment)
        db.flush()

        # 更新账单收款进度
        self._update_payment_progress(db, invoice)

        db.commit()
        db.refresh(payment)
        oplog_service.log(module="billing", action="payment_recorded", db=db,
                          user_id=recorded_by, target_type="payment_record",
                          target_id=payment.id,
                          detail=f"invoice_id={invoice_id}; amount={amount}")
        return payment

    def _update_payment_progress(self, db: Session, invoice: LegalInvoice) -> None:
        """根据已确认付款总额更新账单的收款进度和状态。"""
        paid_total = self._net_paid_total(db, invoice.id)
        total = _d(invoice.total_amount)
        if paid_total >= total and total > ZERO:
            invoice.payment_progress = "fully_paid"
            invoice.status = "paid"
            invoice.paid_at = invoice.paid_at or datetime.now(timezone.utc)
        elif paid_total > ZERO:
            invoice.payment_progress = "partial_paid"
            if invoice.status == "paid":
                invoice.status = "sent"
                invoice.paid_at = None
        else:
            invoice.payment_progress = "unpaid"
            if invoice.status == "paid":
                invoice.status = "sent"
                invoice.paid_at = None

    def _net_paid_total(self, db: Session, invoice_id: int) -> Decimal:
        payments = db.query(sa_func.coalesce(sa_func.sum(LegalPaymentRecord.amount), 0)).filter(
            LegalPaymentRecord.invoice_id == invoice_id,
            LegalPaymentRecord.status == "confirmed",
        ).scalar()
        refunds = db.query(sa_func.coalesce(sa_func.sum(LegalRefundRecord.amount), 0)).filter(
            LegalRefundRecord.invoice_id == invoice_id,
            LegalRefundRecord.status == "completed",
        ).scalar()
        return max(_d(payments) - _d(refunds), ZERO)

    # ── 退款处理 ──────────────────────────────────────────────────

    def request_refund(self, *, db: Session, invoice_id: int, payment_record_id: int | None,
                       organization_id: int, amount: Decimal, reason: str,
                       recorded_by: int) -> LegalRefundRecord:
        """创建退款申请。"""
        invoice = self._get_invoice_or_raise(db, invoice_id)
        if invoice.status not in ("paid", "sent", "overdue"):
            raise ValueError("当前账单状态不允许退款")

        amount = _d(amount)
        if amount <= ZERO:
            raise ValueError("退款金额必须大于零")
        if payment_record_id:
            payment = db.query(LegalPaymentRecord).filter(
                LegalPaymentRecord.id == payment_record_id,
                LegalPaymentRecord.invoice_id == invoice_id,
                LegalPaymentRecord.status == "confirmed",
            ).first()
            if not payment:
                raise ValueError("付款记录不属于该费用通知单或不可退款")
            refunded_for_payment = db.query(sa_func.coalesce(sa_func.sum(LegalRefundRecord.amount), 0)).filter(
                LegalRefundRecord.payment_record_id == payment_record_id,
                LegalRefundRecord.status.in_(("pending", "completed")),
            ).scalar()
            available = _d(payment.amount) - _d(refunded_for_payment)
        else:
            pending_or_done = db.query(sa_func.coalesce(sa_func.sum(LegalRefundRecord.amount), 0)).filter(
                LegalRefundRecord.invoice_id == invoice_id,
                LegalRefundRecord.status.in_(("pending", "completed")),
            ).scalar()
            paid_total = db.query(sa_func.coalesce(sa_func.sum(LegalPaymentRecord.amount), 0)).filter(
                LegalPaymentRecord.invoice_id == invoice_id,
                LegalPaymentRecord.status == "confirmed",
            ).scalar()
            available = _d(paid_total) - _d(pending_or_done)
        if amount > available:
            raise ValueError("退款金额超过可退余额")

        refund = LegalRefundRecord(
            invoice_id=invoice_id,
            payment_record_id=payment_record_id,
            organization_id=organization_id,
            amount=amount,
            reason=reason,
            status="pending",
            recorded_by=recorded_by,
        )
        db.add(refund)

        invoice.payment_progress = "refunding"
        db.commit()
        db.refresh(refund)
        oplog_service.log(module="billing", action="refund_requested", db=db,
                          user_id=recorded_by, target_type="refund_record",
                          target_id=refund.id,
                          detail=f"invoice_id={invoice_id}; amount={amount}")
        return refund

    def approve_refund(self, *, db: Session, refund_id: int, approved_by: int,
                       approved: bool) -> LegalRefundRecord:
        """审批退款申请。"""
        refund = db.query(LegalRefundRecord).filter(LegalRefundRecord.id == refund_id).first()
        if not refund:
            raise ValueError("退款记录不存在")
        if refund.status != "pending":
            raise ValueError("退款申请已处理")

        now = datetime.now(timezone.utc)
        if approved:
            refund.status = "completed"
            refund.approved_by = approved_by
            refund.approved_at = now
            # 重新计算账单进度
            invoice = db.query(LegalInvoice).filter(LegalInvoice.id == refund.invoice_id).first()
            if invoice:
                self._update_payment_progress(db, invoice)
        else:
            refund.status = "rejected"
            refund.approved_by = approved_by
            refund.approved_at = now

        db.commit()
        db.refresh(refund)
        return refund

    # ── 催收提醒 ──────────────────────────────────────────────────

    def create_collection_reminder(self, *, db: Session, invoice_id: int,
                                  organization_id: int, created_by: int,
                                  note: str | None = None) -> LegalCollectionReminder:
        """创建催收提醒记录。"""
        invoice = self._get_invoice_or_raise(db, invoice_id)
        if invoice.status not in ("sent", "overdue"):
            raise ValueError("仅已发送或逾期账单可催收")

        reminder = LegalCollectionReminder(
            invoice_id=invoice_id,
            organization_id=organization_id,
            note=note,
            status="draft",
            created_by=created_by,
        )
        db.add(reminder)

        invoice.collection_count = (invoice.collection_count or 0) + 1
        db.commit()
        db.refresh(reminder)
        return reminder

    def send_collection_reminder(self, *, db: Session, reminder_id: int) -> LegalCollectionReminder:
        """发送催收提醒。"""
        reminder = db.query(LegalCollectionReminder).filter(
            LegalCollectionReminder.id == reminder_id
        ).first()
        if not reminder:
            raise ValueError("催收提醒不存在")
        if reminder.status == "sent":
            return reminder

        invoice = db.query(LegalInvoice).filter(LegalInvoice.id == reminder.invoice_id).first()

        # 尝试发送邮件
        try:
            self._send_collection_email(db=db, invoice=invoice, reminder=reminder)
        except Exception as exc:
            logger.warning("催收邮件发送失败: %s", exc)
            reminder.status = "failed"
            db.commit()
            db.refresh(reminder)
            return reminder

        reminder.status = "sent"
        reminder.sent_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(reminder)
        return reminder

    def _send_collection_email(self, *, db: Session, invoice: LegalInvoice,
                               reminder: LegalCollectionReminder) -> None:
        """发送催收邮件。"""
        target_email = getattr(invoice, "client_contact", None)
        if not target_email:
            return

        try:
            from app.models.email import EmailDraft
            draft = EmailDraft(
                user_id=reminder.created_by,
                organization_id=invoice.organization_id,
                subject=f"付款提醒：账单 {invoice.invoice_no}",
                recipient=target_email,
                content=(
                    f"尊敬的客户，\n\n"
                    f"您的账单 {invoice.invoice_no} 尚未付款。\n"
                    f"金额：{invoice.total_amount}\n"
                    f"到期日：{invoice.due_date or '无'}\n\n"
                    f"请尽快安排付款，谢谢。"
                ),
                purpose="催收提醒",
                status="draft",
                generation_type="collection_reminder",
            )
            db.add(draft)
            db.flush()
        except Exception as exc:
            logger.error("创建催收邮件草稿失败: %s", exc)

    # ── 查询 ──────────────────────────────────────────────────────

    def get_invoice(self, *, db: Session, invoice_id: int) -> LegalInvoice | None:
        return db.query(LegalInvoice).filter(LegalInvoice.id == invoice_id).first()

    def list_invoices(self, *, db: Session, organization_id: int,
                      case_id: int | None = None,
                      status: str | None = None,
                      limit: int = 50) -> list[LegalInvoice]:
        query = db.query(LegalInvoice).filter(
            LegalInvoice.organization_id == organization_id
        )
        if case_id:
            query = query.filter(LegalInvoice.case_id == case_id)
        if status:
            query = query.filter(LegalInvoice.status == status)
        return query.order_by(LegalInvoice.created_at.desc()).limit(limit).all()

    def list_invoice_items(self, *, db: Session, invoice_id: int) -> list[LegalInvoiceItem]:
        return db.query(LegalInvoiceItem).filter(
            LegalInvoiceItem.invoice_id == invoice_id
        ).order_by(LegalInvoiceItem.id).all()

    def get_payment_records(self, *, db: Session, invoice_id: int) -> list[LegalPaymentRecord]:
        return db.query(LegalPaymentRecord).filter(
            LegalPaymentRecord.invoice_id == invoice_id
        ).order_by(LegalPaymentRecord.created_at.desc()).all()

    def serialize_invoice(self, invoice: LegalInvoice) -> dict:
        return {
            "id": invoice.id,
            "organization_id": invoice.organization_id,
            "case_id": invoice.case_id,
            "invoice_no": invoice.invoice_no,
            "client_display_name": invoice.client_display_name,
            "issue_date": str(invoice.issue_date) if invoice.issue_date else None,
            "due_date": str(invoice.due_date) if invoice.due_date else None,
            "billing_period_start": str(invoice.billing_period_start) if invoice.billing_period_start else None,
            "billing_period_end": str(invoice.billing_period_end) if invoice.billing_period_end else None,
            "subtotal": str(invoice.subtotal),
            "tax_amount": str(invoice.tax_amount),
            "discount_amount": str(invoice.discount_amount),
            "total_amount": str(invoice.total_amount),
            "status": invoice.status,
            "payment_progress": invoice.payment_progress,
            "collection_count": invoice.collection_count,
            "sent_at": invoice.sent_at.isoformat() if invoice.sent_at else None,
            "paid_at": invoice.paid_at.isoformat() if invoice.paid_at else None,
            "voided_at": invoice.voided_at.isoformat() if invoice.voided_at else None,
            "void_reason": invoice.void_reason,
            "pdf_path": invoice.pdf_path,
            "created_by": invoice.created_by,
            "created_at": invoice.created_at.isoformat() if invoice.created_at else None,
            "updated_at": invoice.updated_at.isoformat() if invoice.updated_at else None,
        }


billing_service = BillingService()
