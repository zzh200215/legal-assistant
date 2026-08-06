"""Phase 11 — 计时计费 API"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.time import utc_now
from app.core.auth import (
    get_current_user,
    verify_case_access,
    verify_org_member_access,
    verify_org_role_access,
    verify_resource_access,
)
from app.models.user import User
from app.models.org import OrganizationMember, LegalMemberRole
from app.models.legal_billing import (
    LegalTimeEntry, LegalBillingRule, LegalInvoice,
    LegalInvoiceItem, LegalPaymentRecord, LegalRefundRecord, LegalCollectionReminder,
)
from app.services.billing_service import billing_service

router = APIRouter()


# ── Authorization Helpers ─────────────────────────────────────────────────────

def verify_time_entry_access(entry_id: int, user: User, db: Session) -> LegalTimeEntry:
    """验证用户对时间记录的访问权限，返回404而非403"""
    return verify_resource_access("time_entry", entry_id, user.id, db)["resource"]


def verify_invoice_access(invoice_id: int, user: User, db: Session,
                           required_roles: Optional[list] = None) -> LegalInvoice:
    """验证用户对发票的访问权限，返回404而非403"""
    min_role = None
    if required_roles:
        role_hierarchy = {
            LegalMemberRole.admin: 4, LegalMemberRole.reviewer: 3,
            LegalMemberRole.editor: 2, LegalMemberRole.client: 1,
        }
        min_role = min(required_roles, key=lambda role: role_hierarchy.get(role, 0))
    return verify_resource_access(
        "invoice", invoice_id, user.id, db, min_role=min_role
    )["resource"]


# ── Time Entries ──────────────────────────────────────────────────────────────

class TimeEntryCreate(BaseModel):
    case_id: int
    billing_rule_id: Optional[int] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_minutes: Optional[int] = Field(None, ge=1, le=1440)
    description: str = Field(..., min_length=1, max_length=500)
    idempotency_key: Optional[str] = None


class TimeEntryPatch(BaseModel):
    action: Optional[str] = Field(None, description="pause / resume / complete / void")
    ended_at: Optional[datetime] = None
    description: Optional[str] = Field(None, max_length=500)
    billable: Optional[int] = None  # 1=可计费 2=不计费


@router.post("/orgs/{org_id}/cases/{case_id}/time-entries")
def create_time_entry(
    org_id: int,
    case_id: int,
    body: TimeEntryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建计时记录"""
    case_info = verify_case_access(case_id, current_user.id, db)
    if case_info["organization_id"] != org_id:
        raise HTTPException(404, detail="案件不存在")
    if body.case_id != case_id:
        raise HTTPException(400, detail="案件ID不匹配")

    # 幂等键检查
    if body.idempotency_key:
        existing = db.query(LegalTimeEntry).filter(
            LegalTimeEntry.idempotency_key == body.idempotency_key
        ).first()
        if existing:
            return existing

    # 同一用户最多一条 running
    running = db.query(LegalTimeEntry).filter(
        LegalTimeEntry.operator_id == current_user.id,
        LegalTimeEntry.status == "running",
    ).first()
    if running and (not body.ended_at) and (not body.duration_minutes):
        raise HTTPException(409, detail={"code": "TIME_ENTRY_ALREADY_RUNNING", "entry_id": running.id})

    entry = LegalTimeEntry(
        organization_id=org_id,
        case_id=case_id,
        operator_id=current_user.id,
        billing_rule_id=body.billing_rule_id,
        started_at=body.started_at or utc_now(),
        ended_at=body.ended_at,
        duration_minutes=body.duration_minutes,
        status="completed" if body.duration_minutes or body.ended_at else "running",
        description=body.description,
        idempotency_key=body.idempotency_key,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


@router.patch("/time-entries/{entry_id}")
def patch_time_entry(
    entry_id: int,
    body: TimeEntryPatch,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 验证访问权限
    entry = verify_time_entry_access(entry_id, current_user, db)

    # 归属校验：仅本人或 admin/reviewer 可操作/修改他人计时条目，防止篡改他人计费工时
    if entry.operator_id != current_user.id:
        try:
            verify_resource_access("time_entry", entry_id, current_user.id, db, min_role=LegalMemberRole.reviewer)
        except HTTPException:
            raise HTTPException(403, detail="无权操作他人的计时条目")

    action = body.action
    if action is not None:
        allowed_transitions = {
            "running": ["pause", "complete", "void"],
            "paused": ["resume", "void"],
            "completed": ["void"],
        }
        if action not in allowed_transitions.get(entry.status, []):
            raise HTTPException(409, detail=f"Cannot {action} a {entry.status} entry")

    now = utc_now()
    if action == "pause":
        entry.status = "paused"
    elif action == "resume":
        entry.status = "running"
    elif action == "complete":
        entry.status = "completed"
        entry.ended_at = body.ended_at or now
        if entry.started_at and entry.ended_at:
            # SQLite returns naive datetimes; normalize before subtraction
            def _to_naive(dt):
                return dt.replace(tzinfo=None) if dt.tzinfo else dt
            delta = _to_naive(entry.ended_at) - _to_naive(entry.started_at)
            entry.duration_minutes = max(1, int(delta.total_seconds() / 60))
    elif action == "void":
        entry.status = "voided"
    # action is None: billable-only update (no state change)

    if body.description:
        entry.description = body.description
    if body.billable is not None:
        entry.billable = body.billable

    db.commit()
    db.refresh(entry)
    return entry


@router.get("/orgs/{org_id}/cases/{case_id}/time-entries")
def list_time_entries(
    org_id: int,
    case_id: int,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    case_info = verify_case_access(case_id, current_user.id, db)
    if case_info["organization_id"] != org_id:
        raise HTTPException(404, detail="案件不存在")
    q = db.query(LegalTimeEntry).filter(
        LegalTimeEntry.organization_id == org_id,
        LegalTimeEntry.case_id == case_id,
    )
    total = q.count()
    items = q.order_by(LegalTimeEntry.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# ── Billing Rules ─────────────────────────────────────────────────────────────

class BillingRuleCreate(BaseModel):
    case_id: Optional[int] = None
    name: str = Field(..., max_length=128)
    billing_mode: str = Field(..., pattern="^(hourly|fixed_stage|hybrid)$")
    hourly_rate: Optional[Decimal] = Field(None, ge=0)
    fixed_amount: Optional[Decimal] = Field(None, ge=0)
    currency: str = "CNY"


@router.post("/orgs/{org_id}/billing-rules")
def create_billing_rule(
    org_id: int,
    body: BillingRuleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    verify_org_role_access(org_id, current_user.id, LegalMemberRole.admin, db)
    rule = LegalBillingRule(
        organization_id=org_id,
        case_id=body.case_id,
        name=body.name,
        billing_mode=body.billing_mode,
        hourly_rate=body.hourly_rate,
        fixed_amount=body.fixed_amount,
        currency=body.currency,
        created_by=current_user.id,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@router.get("/orgs/{org_id}/billing-rules")
def list_billing_rules(
    org_id: int,
    case_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    verify_org_member_access(org_id, current_user.id, db)
    q = db.query(LegalBillingRule).filter(
        LegalBillingRule.organization_id == org_id,
        LegalBillingRule.is_active == 1,
    )
    if case_id is not None:
        q = q.filter(LegalBillingRule.case_id == case_id)
    return q.all()


# ── Invoices ──────────────────────────────────────────────────────────────────

class InvoiceCreate(BaseModel):
    case_id: int
    client_display_name: str = Field(..., max_length=256)
    issue_date: date
    due_date: Optional[date] = None
    billing_period_start: Optional[date] = None
    billing_period_end: Optional[date] = None
    discount_amount: Decimal = Field(default=Decimal("0"), ge=0)
    tax_rate: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    time_entry_ids: Optional[list[int]] = None
    idempotency_key: Optional[str] = None


def _gen_invoice_no(org_id: int, db: Session) -> str:
    count = db.query(LegalInvoice).filter(LegalInvoice.organization_id == org_id).count()
    return f"INV-{org_id}-{count + 1:05d}"


@router.post("/orgs/{org_id}/invoices")
def create_invoice(
    org_id: int,
    body: InvoiceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    verify_org_role_access(org_id, current_user.id, LegalMemberRole.admin, db)
    try:
        return billing_service.create_invoice(
            db=db, organization_id=org_id, case_id=body.case_id,
            created_by=current_user.id, client_display_name=body.client_display_name,
            issue_date=body.issue_date, due_date=body.due_date,
            billing_period_start=body.billing_period_start,
            billing_period_end=body.billing_period_end,
            discount_amount=body.discount_amount, tax_rate=body.tax_rate,
            time_entry_ids=body.time_entry_ids, idempotency_key=body.idempotency_key,
        )
    except ValueError as exc:
        raise HTTPException(409, detail={"code": "INVOICE_CREATION_REJECTED", "message": str(exc)}) from exc


@router.post("/invoices/{invoice_id}/send")
def send_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 验证访问权限（需要admin或reviewer角色）
    invoice = verify_invoice_access(
        invoice_id, current_user, db,
        required_roles=[LegalMemberRole.admin, LegalMemberRole.reviewer]
    )

    try:
        return billing_service.send_invoice(
            db=db,
            invoice_id=invoice_id,
            user_id=current_user.id,
        )
    except ValueError as exc:
        if str(exc) == "账单不存在":
            raise HTTPException(404) from exc
        raise HTTPException(409, detail={"code": "INVOICE_IMMUTABLE", "message": str(exc)}) from exc


@router.post("/invoices/{invoice_id}/void")
def void_invoice(
    invoice_id: int,
    reason: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 验证访问权限（需要admin或reviewer角色）
    invoice = verify_invoice_access(
        invoice_id, current_user, db,
        required_roles=[LegalMemberRole.admin, LegalMemberRole.reviewer]
    )

    try:
        return billing_service.void_invoice(db=db, invoice_id=invoice_id, user_id=current_user.id, reason=reason)
    except ValueError as exc:
        raise HTTPException(409, detail={"code": "INVOICE_IMMUTABLE", "message": str(exc)}) from exc


@router.post("/invoices/{invoice_id}/payment-webhook")
def payment_webhook(
    invoice_id: int,
    transaction_id: str,
    amount: Decimal,
    provider: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 与 record_payment 一致：仅 admin/reviewer 可确认外部支付到账，避免匿名篡改账单支付状态
    invoice = verify_invoice_access(
        invoice_id, current_user, db,
        required_roles=[LegalMemberRole.admin, LegalMemberRole.reviewer]
    )
    try:
        record = billing_service.record_payment(
            db=db, invoice_id=invoice_id, organization_id=invoice.organization_id,
            amount=amount, payment_method="provider", transaction_id=transaction_id,
            provider=provider, recorded_by=current_user.id,
        )
        return record
    except ValueError as exc:
        raise HTTPException(409, detail={"code": "PAYMENT_REJECTED", "message": str(exc)}) from exc


@router.get("/orgs/{org_id}/invoices")
def list_invoices(
    org_id: int,
    case_id: Optional[int] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    verify_org_member_access(org_id, current_user.id, db)
    q = db.query(LegalInvoice).filter(LegalInvoice.organization_id == org_id)
    if case_id:
        q = q.filter(LegalInvoice.case_id == case_id)
    if status:
        q = q.filter(LegalInvoice.status == status)
    total = q.count()
    items = q.order_by(LegalInvoice.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# ── Payments & Refunds ────────────────────────────────────────────────────────

class PaymentCreate(BaseModel):
    amount: Decimal = Field(..., gt=0)
    payment_method: str = Field(..., pattern="^(provider|bank_transfer|cash|other)$")
    transaction_id: Optional[str] = None
    note: Optional[str] = None


@router.post("/invoices/{invoice_id}/payments")
def record_payment(
    invoice_id: int,
    body: PaymentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 验证访问权限（需要admin或reviewer角色）
    invoice = verify_invoice_access(
        invoice_id, current_user, db,
        required_roles=[LegalMemberRole.admin, LegalMemberRole.reviewer]
    )

    try:
        return billing_service.record_payment(
            db=db, invoice_id=invoice_id, organization_id=invoice.organization_id,
            amount=body.amount, payment_method=body.payment_method,
            transaction_id=body.transaction_id, note=body.note, recorded_by=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(409, detail={"code": "PAYMENT_REJECTED", "message": str(exc)}) from exc


class RefundCreate(BaseModel):
    amount: Decimal = Field(..., gt=0)
    reason: str = Field(..., min_length=1)
    payment_record_id: Optional[int] = None


@router.post("/invoices/{invoice_id}/refunds")
def create_refund(
    invoice_id: int,
    body: RefundCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 验证访问权限（需要admin角色）
    invoice = verify_invoice_access(
        invoice_id, current_user, db,
        required_roles=[LegalMemberRole.admin]
    )

    try:
        return billing_service.request_refund(
            db=db, invoice_id=invoice_id, payment_record_id=body.payment_record_id,
            organization_id=invoice.organization_id, amount=body.amount,
            reason=body.reason, recorded_by=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(409, detail={"code": "REFUND_REJECTED", "message": str(exc)}) from exc


@router.get("/invoices/{invoice_id}/payments")
def list_payments(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    verify_invoice_access(invoice_id, current_user, db)
    return db.query(LegalPaymentRecord).filter(
        LegalPaymentRecord.invoice_id == invoice_id,
    ).order_by(LegalPaymentRecord.created_at.desc()).all()


@router.post("/invoices/{invoice_id}/collection-reminders")
def create_collection_reminder(
    invoice_id: int,
    note: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 验证访问权限（需要admin或reviewer角色）
    invoice = verify_invoice_access(
        invoice_id, current_user, db,
        required_roles=[LegalMemberRole.admin, LegalMemberRole.reviewer]
    )

    try:
        return billing_service.create_collection_reminder(
            db=db, invoice_id=invoice_id, organization_id=invoice.organization_id,
            note=note, created_by=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
