"""门户链接 + OTP 会话 + 门户内容/反馈/下载子路由。"""
import json
import re
import secrets
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.api.legal._portal_helpers import (
    _OTP_LOCK_TTL,
    _OTP_MAX_FAIL,
    _OTP_SEND_MAX,
    _OTP_SEND_WINDOW,
    _OTP_TTL,
    _SESSION_TTL,
    _fail_key,
    _gen_otp,
    _get_active_link,
    _hash_ip,
    _hash_token,
    _mask_email,
    _otp_key,
    _portal_billing_snapshot,
    _redis,
    _require_case_manager,
    _require_organization_member,
    _require_portal_session,
    _send_rate_key,
    _session_key,
    _session_set_key,
    _validate_portal_items,
)
from app.core.auth import get_current_user
from app.core.database import get_db
from app.core.error_codes import PORTAL_LINK_UNAVAILABLE, PORTAL_OTP_INVALID, PORTAL_OTP_LOCKED, err
from app.models.legal_portal import (
    LegalCaseProgressUpdate,
    LegalPortalAccessLog,
    LegalPortalFeedback,
    LegalPortalLink,
    LegalPortalLinkItem,
)
from app.models.org import Organization, OrganizationMember
from app.models.user import User

_ALLOWED_EXPIRES_DAYS = {7, 30, 90}
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

router = APIRouter()

class PortalLinkCreate(BaseModel):
    client_email: str = Field(..., max_length=256, description="客户邮箱，必填，用于 OTP 验证")
    expires_days: int = Field(default=30, description="链接有效天数，只允许 7/30/90，默认 30（#93）")
    max_access_count: Optional[int] = Field(None, ge=1)
    require_email_verification: int = Field(1, ge=0, le=1)
    aggregate_case: int = Field(0, ge=0, le=1,
                                description="1=聚合该案件全部已发布客户可见内容（一个案件一个URL）")
    items: List[dict] = Field(default_factory=list, description="[{item_type, item_id}]")

    @field_validator("client_email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError("client_email 格式无效")
        return v.lower().strip()

    @field_validator("expires_days")
    @classmethod
    def validate_expires_days(cls, v: int) -> int:
        if v not in _ALLOWED_EXPIRES_DAYS:
            raise ValueError(f"expires_days 只允许 {sorted(_ALLOWED_EXPIRES_DAYS)} 之一")
        return v


@router.post("/orgs/{org_id}/cases/{case_id}/portal-links")
def create_portal_link(
    org_id: int,
    case_id: int,
    body: PortalLinkCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from datetime import timedelta

    from app.services.org import security_audit_service

    case = _require_case_manager(db, current_user.id, org_id, case_id)
    items = _validate_portal_items(db, case, body.items)

    # 若关闭邮箱验证，须是组织 admin 且内容仅限进度更新（低敏感）
    require_verification = body.require_email_verification
    has_document = any(i.get("item_type") == "document" for i in items)
    if require_verification == 0:
        member = db.query(OrganizationMember).filter(
            OrganizationMember.organization_id == org_id,
            OrganizationMember.user_id == current_user.id,
        ).first()
        if not member or member.legal_role != "admin":
            raise HTTPException(403, detail="仅组织管理员可关闭邮箱验证")
        if has_document:
            raise HTTPException(400, detail="含文件附件的门户链接必须开启邮箱验证")
        # 写审计事件：管理员关闭了验证
        security_audit_service.write_event(
            event_type="portal_access",
            actor_type="user",
            result="success",
            organization_id=org_id,
            actor_id=str(current_user.id),
            target_type="portal_link",
            detail_json_hash=f"disable_verification:case_id={case_id}",
            db=db,
        )

    # 聚合案件门户聚合全部已发布内容（含文书），一律要求邮箱验证
    if body.aggregate_case and require_verification == 0:
        raise HTTPException(400, detail="聚合案件门户必须开启邮箱验证")

    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw_token)
    token_prefix = raw_token[:8]

    expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_days)

    link = LegalPortalLink(
        organization_id=case.organization_id,
        case_id=case_id,
        token_hash=token_hash,
        token_prefix=token_prefix,
        client_email=body.client_email,
        expires_at=expires_at,
        is_permanent=0,
        max_access_count=body.max_access_count,
        require_email_verification=require_verification,
        aggregate_case=body.aggregate_case,
        created_by=current_user.id,
    )
    db.add(link)
    db.flush()

    for item in items:
        db.add(LegalPortalLinkItem(
            portal_link_id=link.id,
            item_type=item.get("item_type"),
            item_id=item.get("item_id"),
        ))

    db.commit()
    db.refresh(link)
    return {"link": link, "token": raw_token, "token_prefix": token_prefix}


@router.post("/portal-links/{link_id}/revoke")
def revoke_portal_link(
    link_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.services.org import security_audit_service

    link = db.query(LegalPortalLink).filter(LegalPortalLink.id == link_id).first()
    if not link:
        raise HTTPException(404)
    _require_case_manager(db, current_user.id, link.organization_id, link.case_id)
    link.status = "revoked"
    link.revoked_at = datetime.now(timezone.utc)
    link.revoked_by = current_user.id
    db.commit()

    # 撤销后立即清除所有关联 Redis 会话
    try:
        r = _redis()
        sset_key = _session_set_key(link_id)
        session_tokens = r.smembers(sset_key)
        if session_tokens:
            r.delete(*[_session_key(t) for t in session_tokens])
        r.delete(sset_key)
    except Exception:
        pass  # Redis 不可用时不阻断撤销流程

    security_audit_service.write_event(
        event_type="portal_access",
        actor_type="user",
        result="success",
        organization_id=link.organization_id,
        actor_id=str(current_user.id),
        target_type="portal_link",
        target_id=str(link_id),
        detail_json_hash="revoke",
        db=db,
    )

    db.refresh(link)
    return link


@router.get("/orgs/{org_id}/cases/{case_id}/portal-links")
def list_portal_links(
    org_id: int,
    case_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_case_manager(db, current_user.id, org_id, case_id)
    return db.query(LegalPortalLink).filter(
        LegalPortalLink.case_id == case_id,
        LegalPortalLink.organization_id == org_id,
    ).all()


@router.post("/portal/{token}/send-otp")
def portal_send_otp(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """向门户客户邮箱发送 6 位验证码（5 分钟有效）。每个令牌限 3 次/10 分钟。"""
    token_hash = _hash_token(token)
    link = _get_active_link(token_hash, db)

    if not link.client_email:
        raise HTTPException(400, detail="该门户链接未绑定客户邮箱，无法发送验证码")

    r = _redis()
    # 锁定检查（5次连续验证失败后锁定）
    if r.exists(_fail_key(token_hash)):
        raise HTTPException(429, detail=err(PORTAL_OTP_LOCKED))

    # 发送频率限制：每 10 分钟最多发送 3 次
    send_key = _send_rate_key(token_hash)
    send_count = r.incr(send_key)
    if send_count == 1:
        r.expire(send_key, _OTP_SEND_WINDOW)
    if send_count > _OTP_SEND_MAX:
        raise HTTPException(429, detail="验证码发送次数过多，请稍后再试")

    otp = _gen_otp()
    from app.services.notification.outbound_email_service import outbound_email_service
    sender = db.query(User).filter(User.id == link.created_by).first()
    if not sender:
        raise HTTPException(503, detail="门户邮件配置不可用")
    try:
        outbound_email_service.send_portal_otp(
            db=db, user=sender, recipient=link.client_email, otp=otp,
        )
    except (ValueError, OSError) as exc:
        raise HTTPException(503, detail="门户验证码邮件暂不可用") from exc
    r.setex(_otp_key(token_hash), _OTP_TTL, otp)

    ip_hash = _hash_ip(request.client.host if request.client else None)
    db.add(LegalPortalAccessLog(
        portal_link_id=link.id,
        organization_id=link.organization_id,
        ip_hash=ip_hash,
        accessed_at=datetime.now(timezone.utc),
        action="otp_send",
        result="success",
    ))
    db.commit()

    return {"sent": True, "email_masked": _mask_email(link.client_email), "ttl_seconds": _OTP_TTL}


@router.post("/portal/{token}/verify")
def portal_verify_otp(
    token: str,
    otp: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """校验验证码，通过后返回短期门户会话 token（最长 8 小时有效）。"""
    token_hash = _hash_token(token)
    link = _get_active_link(token_hash, db)
    ip_hash = _hash_ip(request.client.host if request.client else None)

    r = _redis()
    fail_key = _fail_key(token_hash)
    otp_key = _otp_key(token_hash)

    # 锁定检查
    if r.exists(fail_key):
        raise HTTPException(429, detail=err(PORTAL_OTP_LOCKED))

    stored = r.get(otp_key)
    if not stored or stored != otp.strip():
        fail_count = r.incr(fail_key)
        if fail_count == 1:
            r.expire(fail_key, _OTP_LOCK_TTL)
        remaining = max(0, _OTP_MAX_FAIL - int(fail_count))
        db.add(LegalPortalAccessLog(
            portal_link_id=link.id,
            organization_id=link.organization_id,
            ip_hash=ip_hash,
            accessed_at=datetime.now(timezone.utc),
            action="otp_verify",
            result="failure",
        ))
        db.commit()
        if int(fail_count) >= _OTP_MAX_FAIL:
            raise HTTPException(429, detail=err(PORTAL_OTP_LOCKED))
        raise HTTPException(400, detail={**err(PORTAL_OTP_INVALID), "remaining_attempts": remaining})

    # 验证成功：清除计数，签发会话 token
    r.delete(otp_key)
    r.delete(fail_key)

    session_token = secrets.token_urlsafe(32)
    r.setex(_session_key(session_token), _SESSION_TTL, str(link.id))
    # 将此会话 token 加入该链接的会话集合，供撤销时批量清除
    try:
        sset_key = _session_set_key(link.id)
        r.sadd(sset_key, session_token)
        r.expire(sset_key, _SESSION_TTL)
    except Exception:
        pass

    db.add(LegalPortalAccessLog(
        portal_link_id=link.id,
        organization_id=link.organization_id,
        ip_hash=ip_hash,
        accessed_at=datetime.now(timezone.utc),
        action="otp_verify",
        result="success",
    ))
    db.commit()

    return {"session_token": session_token, "expires_in": _SESSION_TTL}


@router.get("/portal/{token}/content")
def portal_get_content(
    token: str,
    request: Request,
    x_portal_session: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """返回该门户链接发布的内容摘要。令牌无效/撤销/过期时统一返回 404 + PORTAL_LINK_UNAVAILABLE。"""
    token_hash = _hash_token(token)
    link = _get_active_link(token_hash, db)
    _require_portal_session(link, x_portal_session)

    items = db.query(LegalPortalLinkItem).filter(
        LegalPortalLinkItem.portal_link_id == link.id,
    ).all()

    ip_hash = _hash_ip(request.client.host if request.client else None)
    db.add(LegalPortalAccessLog(
        portal_link_id=link.id,
        organization_id=link.organization_id,
        ip_hash=ip_hash,
        accessed_at=datetime.now(timezone.utc),
        action="view",
        result="success",
    ))
    link.access_count = (link.access_count or 0) + 1
    link.last_accessed_at = datetime.now(timezone.utc)
    db.commit()

    progress_updates = []
    documents = []
    from app.models.document import Document

    if link.aggregate_case:
        # 聚合：#79 P2 一个案件一个URL——自动包含该案全部已发布客户可见内容
        all_updates = db.query(LegalCaseProgressUpdate).filter(
            LegalCaseProgressUpdate.case_id == link.case_id,
            LegalCaseProgressUpdate.organization_id == link.organization_id,
            LegalCaseProgressUpdate.visibility == "client_visible",
            LegalCaseProgressUpdate.status == "published",
        ).all()
        progress_updates = [{"id": u.id, "title": u.title, "body": u.body,
                             "next_steps": u.next_steps,
                             "published_at": u.published_at, "status": u.status}
                            for u in all_updates]
        all_docs = db.query(Document).filter(
            Document.organization_id == link.organization_id,
            Document.download_enabled.is_(True),
        ).all()
        for document in all_docs:
            try:
                metadata = json.loads(document.metadata_json or "{}") if document else {}
            except (TypeError, ValueError):
                metadata = {}
            if metadata.get("case_id") == link.case_id:
                documents.append({"id": document.id, "title": document.title, "file_type": document.file_type})
    else:
        for item in items:
            if item.item_type == "progress_update":
                update = db.query(LegalCaseProgressUpdate).filter(
                    LegalCaseProgressUpdate.id == item.item_id,
                    LegalCaseProgressUpdate.case_id == link.case_id,
                    LegalCaseProgressUpdate.organization_id == link.organization_id,
                    LegalCaseProgressUpdate.visibility == "client_visible",
                    LegalCaseProgressUpdate.status == "published",
                ).first()
                if update:
                    progress_updates.append({"id": update.id, "title": update.title, "body": update.body,
                                             "next_steps": update.next_steps,
                                             "published_at": update.published_at,
                                             "status": update.status})
            elif item.item_type == "document":
                document = db.query(Document).filter(
                    Document.id == item.item_id,
                    Document.organization_id == link.organization_id,
                    Document.download_enabled.is_(True),
                ).first()
                try:
                    metadata = json.loads(document.metadata_json or "{}") if document else {}
                except (TypeError, ValueError):
                    metadata = {}
                if document and metadata.get("case_id") == link.case_id:
                    documents.append({"id": document.id, "title": document.title, "file_type": document.file_type})

    # #93：进展按发布时间倒序（时间线展示）
    progress_updates.sort(key=lambda u: u["published_at"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    org = db.query(Organization).filter(Organization.id == link.organization_id).first()

    return {
        "link_id": link.id,
        "case_id": link.case_id,
        "progress_updates": progress_updates,
        "documents": documents,
        "invoice": _portal_billing_snapshot(db, link),
        "organization": {
            "name": org.name if org else None,
            "portal_logo_url": org.portal_logo_url if org else None,
            "portal_welcome_message": org.portal_welcome_message if org else None,
        },
    }


class PortalFeedbackIn(BaseModel):
    score: int = Field(..., ge=-1, le=1, description="1=有帮助 / -1=待改进")
    note: Optional[str] = Field(None, max_length=500, description="待改进时的补充说明，≤500字")


@router.post("/portal/{token}/feedback")
def portal_submit_feedback(
    token: str,
    body: PortalFeedbackIn,
    request: Request,
    x_portal_session: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """客户对律师服务的反馈（👍/👎 + 备注）。令牌鉴权同 content，落到 legal_portal_feedback。"""
    token_hash = _hash_token(token)
    link = _get_active_link(token_hash, db)
    _require_portal_session(link, x_portal_session)
    if body.score == 0:
        raise HTTPException(422, detail="score 只允许 1（有帮助）或 -1（待改进）")

    ip_hash = _hash_ip(request.client.host if request.client else None)
    db.add(LegalPortalFeedback(
        portal_link_id=link.id,
        organization_id=link.organization_id,
        case_id=link.case_id,
        score=body.score,
        note=(body.note or "").strip() or None,
    ))
    db.add(LegalPortalAccessLog(
        portal_link_id=link.id,
        organization_id=link.organization_id,
        ip_hash=ip_hash,
        action="feedback",
        result="success",
    ))
    db.commit()
    return {"ok": True}


@router.get("/orgs/{org_id}/portal-feedback")
def list_portal_feedback(
    org_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """管理端：某组织下所有门户反馈（按时间倒序）。"""
    _require_organization_member(db, org_id, current_user.id)
    rows = db.query(LegalPortalFeedback).filter(
        LegalPortalFeedback.organization_id == org_id,
    ).order_by(LegalPortalFeedback.created_at.desc()).all()
    return [
        {
            "id": r.id,
            "case_id": r.case_id,
            "score": r.score,
            "note": r.note,
            "created_at": r.created_at,
        }
        for r in rows
    ]


@router.get("/portal/{token}/documents/{document_id}/download")
def portal_download_document(
    token: str,
    document_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    x_portal_session: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Deliver only a document explicitly published on this portal link."""
    token_hash = _hash_token(token)
    link = _get_active_link(token_hash, db)
    _require_portal_session(link, x_portal_session)
    ip_hash = _hash_ip(request.client.host if request.client else None)

    # 非聚合链接仅允许下载已显式发布到该链接的文书；聚合链接放开到案件维度（仍校验属于该案）
    if not link.aggregate_case:
        item = db.query(LegalPortalLinkItem).filter(
            LegalPortalLinkItem.portal_link_id == link.id,
            LegalPortalLinkItem.item_type == "document",
            LegalPortalLinkItem.item_id == document_id,
        ).first()
        if not item:
            raise HTTPException(404, detail=err(PORTAL_LINK_UNAVAILABLE))

    from app.models.document import Document
    from app.services.documents.document_delivery_service import DocumentDeliveryError, document_delivery_service
    document = db.query(Document).filter(
        Document.id == document_id,
        Document.organization_id == link.organization_id,
        Document.download_enabled.is_(True),
    ).first()
    try:
        metadata = json.loads(document.metadata_json or "{}") if document else {}
    except (TypeError, ValueError):
        metadata = {}
    if not document or metadata.get("case_id") != link.case_id:
        raise HTTPException(404, detail=err(PORTAL_LINK_UNAVAILABLE))

    # 若文件要求水印且当前无法生成水印，拒绝下载而非返回原文件
    if document.watermark_required:
        try:
            delivery = document_delivery_service.prepare_download(document=document, user=User(id=0, username="门户访客"))
        except DocumentDeliveryError as exc:
            db.add(LegalPortalAccessLog(
                portal_link_id=link.id,
                organization_id=link.organization_id,
                ip_hash=ip_hash,
                action="download",
                resource_type="document",
                resource_id=document.id,
                result="error",
            ))
            db.commit()
            raise HTTPException(409, detail="文件水印处理失败，暂不可下载") from exc
    else:
        visitor = User(id=0, username="门户访客")
        try:
            delivery = document_delivery_service.prepare_download(document=document, user=visitor)
        except DocumentDeliveryError as exc:
            raise HTTPException(409, detail="客户文件暂不可下载") from exc

    db.add(LegalPortalAccessLog(
        portal_link_id=link.id,
        organization_id=link.organization_id,
        ip_hash=ip_hash,
        action="download",
        resource_type="document",
        resource_id=document.id,
        result="success",
    ))
    db.commit()
    if delivery["temporary"]:
        background_tasks.add_task(document_delivery_service.cleanup, delivery["path"])
    return FileResponse(delivery["path"], filename=delivery["filename"],
                        media_type=delivery["media_type"], background=background_tasks)

