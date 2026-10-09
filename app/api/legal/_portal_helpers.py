"""客户门户共享助手：OTP/会话 Redis 键、鉴权守卫、链接/内容校验、脱敏。"""
import hashlib
import json
import secrets
import string
from datetime import datetime, timezone
from typing import List

import redis as redis_lib
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.auth import verify_case_access
from app.core.config import get_settings
from app.core.error_codes import PORTAL_LINK_UNAVAILABLE, PORTAL_OTP_INVALID, err
from app.models.legal import LegalCase
from app.models.legal_portal import LegalCaseProgressUpdate, LegalPortalLink
from app.models.org import OrganizationMember

_OTP_TTL = 300
_OTP_MAX_FAIL = 5
_OTP_LOCK_TTL = 900
_SESSION_TTL = 28800
_OTP_SEND_MAX = 3
_OTP_SEND_WINDOW = 600

def _redis():
    return redis_lib.from_url(get_settings().REDIS_URL, decode_responses=True)


def _otp_key(token_hash: str) -> str:
    return f"portal_otp:{token_hash}"


def _fail_key(token_hash: str) -> str:
    return f"portal_otp_fail:{token_hash}"


def _send_rate_key(token_hash: str) -> str:
    return f"portal_otp_send_rate:{token_hash}"


def _session_key(session_token: str) -> str:
    return f"portal_session:{session_token}"


def _session_set_key(link_id: int) -> str:
    """Redis set 记录一个门户链接下的所有活跃会话 token，供撤销时批量删除。"""
    return f"portal_sessions:{link_id}"


def _hash_ip(ip: str | None) -> str:
    if not ip:
        return "unknown"
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


def _gen_otp() -> str:
    return "".join(secrets.choice(string.digits) for _ in range(6))


def _require_case_manager(db: Session, user_id: int, org_id: int, case_id: int) -> LegalCase:
    case = db.query(LegalCase).filter(
        LegalCase.id == case_id,
        LegalCase.organization_id == org_id,
    ).first()
    if not case:
        raise HTTPException(404, detail="案件不存在")
    member = db.query(OrganizationMember).filter(
        OrganizationMember.organization_id == org_id,
        OrganizationMember.user_id == user_id,
    ).first()
    if not member or member.legal_role not in ("admin", "reviewer"):
        raise HTTPException(403, detail="仅组织管理员或审核律师可管理案件")
    # 严格案件必须是活跃案件成员，不能只凭组织高角色管理。
    verify_case_access(case_id, user_id, db)
    return case


def _require_organization_member(db: Session, org_id: int, user_id: int) -> OrganizationMember:
    member = db.query(OrganizationMember).filter(
        OrganizationMember.organization_id == org_id,
        OrganizationMember.user_id == user_id,
    ).first()
    if not member:
        raise HTTPException(400, detail="用户不是该组织成员")
    return member


def _require_progress_editor(db: Session, user_id: int, org_id: int, case_id: int) -> LegalCase:
    """客户进度由 editor 起草，reviewer/admin 负责审核发布。"""
    case = db.query(LegalCase).filter(
        LegalCase.id == case_id, LegalCase.organization_id == org_id,
    ).first()
    if not case:
        raise HTTPException(404, detail="案件不存在")
    member = _require_organization_member(db, org_id, user_id)
    if member.legal_role not in ("admin", "reviewer", "editor"):
        raise HTTPException(403, detail="当前角色不能创建案件进度")
    verify_case_access(case_id, user_id, db)
    return case


def _require_portal_session(link: LegalPortalLink, session_token: str | None) -> None:
    if not link.require_email_verification:
        return
    if not session_token:
        raise HTTPException(401, detail=err(PORTAL_OTP_INVALID))
    try:
        linked_id = _redis().get(_session_key(session_token))
    except Exception as exc:
        raise HTTPException(503, detail="门户验证服务暂不可用") from exc
    if linked_id != str(link.id):
        raise HTTPException(401, detail=err(PORTAL_OTP_INVALID))


def _portal_billing_snapshot(db: Session, link: LegalPortalLink) -> dict | None:
    """按案件维度返回客户可见的非草稿发票对账摘要（P3）。

    不返回客户联系方式、支付流水号、内部备注或操作人；只保留客户核对账单所需的
    发票明细、已确认收款和应收余额。调用方仍可把 ``latest`` 映射为旧版 ``invoice`` 字段。
    """
    from app.models.legal_billing import LegalInvoice, LegalInvoiceItem, LegalPaymentRecord
    invoices = (
        db.query(LegalInvoice)
        .filter(
            LegalInvoice.organization_id == link.organization_id,
            LegalInvoice.case_id == link.case_id,
            LegalInvoice.status != "draft",
        )
        .order_by(LegalInvoice.issue_date.desc(), LegalInvoice.id.desc())
        .all()
    )
    if not invoices:
        return None

    def serialize_invoice(invoice: LegalInvoice) -> dict:
        items = db.query(LegalInvoiceItem).filter(LegalInvoiceItem.invoice_id == invoice.id).all()
        payments = db.query(LegalPaymentRecord).filter(
            LegalPaymentRecord.invoice_id == invoice.id,
            LegalPaymentRecord.status == "confirmed",
        ).order_by(LegalPaymentRecord.recorded_at.desc(), LegalPaymentRecord.id.desc()).all()
        paid_total = sum(float(payment.amount or 0) for payment in payments)
        total_amount = float(invoice.total_amount or 0)
        return {
            "id": invoice.id,
            "invoice_number": invoice.invoice_no,
            "currency": invoice.currency or "CNY",
            "subtotal": float(invoice.subtotal or 0),
            "tax_amount": float(invoice.tax_amount or 0),
            "discount_amount": float(invoice.discount_amount or 0),
            "total_amount": total_amount,
            "status": invoice.status,
            "payment_progress": invoice.payment_progress,
            "issue_date": invoice.issue_date.isoformat() if invoice.issue_date else None,
            "period_start": invoice.billing_period_start.isoformat() if invoice.billing_period_start else None,
            "period_end": invoice.billing_period_end.isoformat() if invoice.billing_period_end else None,
            "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
            "paid_amount": round(paid_total, 2),
            "outstanding_amount": round(max(total_amount - paid_total, 0), 2),
            "items": [
                {
                    "title": item.title,
                    "description": item.description,
                    "quantity": float(item.quantity or 0),
                    "unit_price": float(item.unit_price or 0),
                    "amount": float(item.amount or 0),
                }
                for item in items
            ],
            "payments": [
                {
                    "amount": float(payment.amount or 0),
                    "currency": payment.currency or invoice.currency or "CNY",
                    "payment_method": payment.payment_method,
                    "recorded_at": payment.recorded_at,
                }
                for payment in payments
            ],
        }

    serialized = [serialize_invoice(invoice) for invoice in invoices]
    return {
        "currency": invoices[0].currency or "CNY",
        "total_invoiced": round(sum(item["total_amount"] for item in serialized), 2),
        "total_paid": round(sum(item["paid_amount"] for item in serialized), 2),
        "total_outstanding": round(sum(item["outstanding_amount"] for item in serialized), 2),
        "latest": serialized[0],
        "invoices": serialized,
    }


def _validate_portal_items(db: Session, case: LegalCase, items: List[dict]) -> list[dict]:
    allowed_types = {"progress_update", "document"}
    normalized: list[dict] = []
    seen: set[tuple[str, int]] = set()
    from app.models.document import Document

    for item in items:
        item_type = str(item.get("item_type") or "")
        item_id = item.get("item_id")
        if item_type not in allowed_types or not isinstance(item_id, int) or item_id <= 0:
            raise HTTPException(400, detail="门户内容类型或资源编号无效")
        key = (item_type, item_id)
        if key in seen:
            continue
        seen.add(key)
        if item_type == "progress_update":
            update = db.query(LegalCaseProgressUpdate).filter(
                LegalCaseProgressUpdate.id == item_id,
                LegalCaseProgressUpdate.case_id == case.id,
                LegalCaseProgressUpdate.organization_id == case.organization_id,
                LegalCaseProgressUpdate.visibility == "client_visible",
                LegalCaseProgressUpdate.status == "published",
            ).first()
            if not update:
                raise HTTPException(400, detail="进度更新尚未发布给客户或不属于该案件")
        else:
            document = db.query(Document).filter(
                Document.id == item_id,
                Document.organization_id == case.organization_id,
                Document.download_enabled.is_(True),
            ).first()
            if not document:
                raise HTTPException(400, detail="文档不可发布到客户门户")
            try:
                metadata = json.loads(document.metadata_json or "{}")
            except (TypeError, ValueError):
                metadata = {}
            if metadata.get("case_id") != case.id:
                raise HTTPException(400, detail="文档未关联到该案件，不能发布到客户门户")
        normalized.append({"item_type": item_type, "item_id": item_id})
    return normalized


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _get_active_link(token_hash: str, db: Session) -> LegalPortalLink:
    link = db.query(LegalPortalLink).filter(
        LegalPortalLink.token_hash == token_hash,
        LegalPortalLink.status == "active",
    ).first()
    if not link:
        raise HTTPException(404, detail=err(PORTAL_LINK_UNAVAILABLE))
    now = datetime.now(timezone.utc)
    expires_at = link.expires_at
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at and expires_at < now:
        link.status = "expired"
        db.commit()
        raise HTTPException(404, detail=err(PORTAL_LINK_UNAVAILABLE))
    if link.max_access_count and link.access_count >= link.max_access_count:
        link.status = "access_limited"
        db.commit()
        raise HTTPException(404, detail=err(PORTAL_LINK_UNAVAILABLE))
    return link


def _mask_email(email: str) -> str:
    if "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    return local[:2] + "***@" + domain

