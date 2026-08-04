"""#83/平台收款 API（对公转账：提交 → 管理员确认/驳回 → 激活订阅 + 开票信息快照）"""

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.api_response import api_error, paginated_payload
from app.core.auth import get_current_user, require_admin_user
from app.core.database import get_db
from app.models.org import OrganizationMember
from app.models.platform_payment import PlatformPayment
from app.models.subscription import SubscriptionPlan
from app.models.user import User
from app.services.oplog_service import oplog_service
from app.services.subscription_service import subscription_service

router = APIRouter()

VALID_TIERS = ("pro", "team")


class BankTransferRequest(BaseModel):
    plan_tier: str = Field(..., description="pro / team")
    amount: float = Field(..., gt=0)
    voucher_no: Optional[str] = Field(None, max_length=128)
    note: Optional[str] = Field(None, max_length=2000)


class ConfirmRequest(BaseModel):
    invoice_snapshot: Optional[dict] = None
    note: Optional[str] = Field(None, max_length=2000)


def _user_org_id(user_id: int, db: Session) -> int:
    member = (
        db.query(OrganizationMember)
        .filter(OrganizationMember.user_id == user_id)
        .order_by(OrganizationMember.organization_id.asc())
        .first()
    )
    if not member:
        raise api_error(403, "请先加入一个组织后再提交对公转账", code="NOT_ORG_MEMBER")
    return member.organization_id


def _plan_price(db: Session, tier: str) -> Decimal:
    subscription_service.ensure_default_plans(db)
    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.tier == tier).first()
    if not plan:
        raise api_error(400, f"无效的计划类型: {tier}", code="INVALID_TIER")
    return Decimal(plan.price_monthly)


@router.post("/payments/bank-transfer")
def submit_bank_transfer(
    req: BankTransferRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if req.plan_tier not in VALID_TIERS:
        raise api_error(400, f"无效的计划类型，可选: {VALID_TIERS}", code="INVALID_TIER")
    expected = _plan_price(db, req.plan_tier)
    if Decimal(str(req.amount)) != expected:
        raise api_error(400, f"转账金额与 {req.plan_tier} 档不符，应为 ¥{expected}", code="AMOUNT_MISMATCH")

    payment = PlatformPayment(
        organization_id=_user_org_id(current_user.id, db),
        user_id=current_user.id,
        plan_tier=req.plan_tier,
        amount=expected,
        currency="CNY",
        status="pending",
        voucher_no=req.voucher_no,
        note=req.note,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    oplog_service.log(
        module="platform_payment", action="bank_transfer_submitted", db=db,
        user_id=current_user.id, target_type="platform_payment", target_id=payment.id,
        detail=f"plan={req.plan_tier}; amount={expected}",
    )
    return {"payment_id": payment.id, "status": "pending"}


@router.post("/payments/{payment_id}/confirm")
def confirm_bank_transfer(
    payment_id: int,
    req: ConfirmRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    payment = db.query(PlatformPayment).filter(PlatformPayment.id == payment_id).first()
    if not payment:
        raise api_error(404, "收款记录不存在", code="PLATFORM_PAYMENT_NOT_FOUND")
    if payment.status != "pending":
        raise api_error(400, f"当前状态 {payment.status} 不可确认", code="PAYMENT_ALREADY_PROCESSED")

    payment.status = "confirmed"
    payment.confirmed_by = current_user.id
    payment.confirmed_at = datetime.now(timezone.utc)
    if req.note:
        payment.note = (payment.note or "") + f"\n[确认备注] {req.note}"
    if req.invoice_snapshot is not None:
        payment.invoice_snapshot_json = json.dumps(req.invoice_snapshot, ensure_ascii=False)

    member = (
        db.query(OrganizationMember)
        .filter(OrganizationMember.organization_id == payment.organization_id)
        .order_by(OrganizationMember.organization_id.asc())
        .first()
    )
    if not member:
        raise api_error(400, "付款组织无有效成员，无法激活订阅", code="ORG_NO_MEMBER")

    subscription_service.ensure_default_plans(db)
    subscription_service.activate_subscription(
        db=db,
        user_id=member.user_id,
        plan_tier=payment.plan_tier,
        payment_provider="bank_transfer",
        payment_subscription_id=f"platform_payment_{payment.id}",
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    oplog_service.log(
        module="platform_payment", action="bank_transfer_confirmed", db=db,
        user_id=current_user.id, target_type="platform_payment", target_id=payment.id,
        detail=f"plan={payment.plan_tier}; activated user_id={member.user_id}",
    )
    return {"payment_id": payment.id, "status": "confirmed", "activated_user_id": member.user_id}


@router.post("/payments/{payment_id}/reject")
def reject_bank_transfer(
    payment_id: int,
    req: ConfirmRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    payment = db.query(PlatformPayment).filter(PlatformPayment.id == payment_id).first()
    if not payment:
        raise api_error(404, "收款记录不存在", code="PLATFORM_PAYMENT_NOT_FOUND")
    if payment.status != "pending":
        raise api_error(400, f"当前状态 {payment.status} 不可驳回", code="PAYMENT_ALREADY_PROCESSED")
    payment.status = "rejected"
    if req.note:
        payment.note = (payment.note or "") + f"\n[驳回备注] {req.note}"
    db.add(payment)
    db.commit()
    oplog_service.log(
        module="platform_payment", action="bank_transfer_rejected", db=db,
        user_id=current_user.id, target_type="platform_payment", target_id=payment.id,
        detail=req.note or "",
    )
    return {"payment_id": payment.id, "status": "rejected"}


@router.get("/payments")
def list_payments(
    status: str = Query("pending", description="pending / confirmed / rejected / all"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    q = db.query(PlatformPayment)
    if status != "all":
        q = q.filter(PlatformPayment.status == status)
    total = q.count()
    rows = (
        q.order_by(PlatformPayment.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = []
    for p in rows:
        item = {
            "id": p.id,
            "organization_id": p.organization_id,
            "user_id": p.user_id,
            "plan_tier": p.plan_tier,
            "amount": float(p.amount),
            "currency": p.currency,
            "status": p.status,
            "voucher_no": p.voucher_no,
            "note": p.note,
            "confirmed_at": str(p.confirmed_at) if p.confirmed_at else None,
            "created_at": str(p.created_at),
        }
        if p.invoice_snapshot_json:
            try:
                item["invoice_snapshot"] = json.loads(p.invoice_snapshot_json)
            except json.JSONDecodeError:
                item["invoice_snapshot"] = None
        items.append(item)
    return paginated_payload(items, total=total, page=page, page_size=page_size)
