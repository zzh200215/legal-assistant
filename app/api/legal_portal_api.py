"""Phase 11 — 关键日期 / 门户链接 / 案件成员 / 进度更新 API"""

import hashlib
import json
import re
import secrets
import string
from datetime import datetime, timezone
from typing import List, Optional

import redis as redis_lib
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Header, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.auth import get_current_user
from app.core.auth import verify_case_access
from app.core.error_codes import err, PORTAL_LINK_UNAVAILABLE, PORTAL_OTP_INVALID, PORTAL_OTP_LOCKED
from app.models.user import User
from app.models.legal import LegalCase
from app.models.org import OrganizationMember, Organization
from app.models.legal_portal import (
    LegalDeadline, LegalPortalLink, LegalPortalLinkItem, LegalPortalAccessLog,
    LegalCaseMember, LegalCaseProgressUpdate, LegalCaseProgressRead,
)

router = APIRouter()

# ── Redis OTP helpers ─────────────────────────────────────────────────────────

_OTP_TTL = 300        # 5 分钟
_OTP_MAX_FAIL = 5
_OTP_LOCK_TTL = 900   # 锁定 15 分钟
_SESSION_TTL = 28800  # 门户会话最长 8 小时（PRD 要求）
_OTP_SEND_MAX = 3     # 每个令牌每窗口最多发送次数
_OTP_SEND_WINDOW = 600  # 发送速率窗口（10 分钟）


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


class PortalBrandingIn(BaseModel):
    portal_logo_url: str | None = Field(None, max_length=512)
    portal_welcome_message: str | None = Field(None, max_length=256)


@router.get("/orgs/{org_id}/portal-branding")
def get_portal_branding(
    org_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_organization_member(db, org_id, current_user.id)
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(404, detail="组织不存在")
    return {
        "organization_id": org.id,
        "name": org.name,
        "portal_logo_url": org.portal_logo_url,
        "portal_welcome_message": org.portal_welcome_message,
    }


@router.put("/orgs/{org_id}/portal-branding")
def update_portal_branding(
    org_id: int,
    body: PortalBrandingIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    member = _require_organization_member(db, org_id, current_user.id)
    if member.legal_role not in ("admin", "reviewer"):
        raise HTTPException(403, detail="仅组织管理员或审核律师可配置门户品牌")
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(404, detail="组织不存在")
    org.portal_logo_url = (body.portal_logo_url or "").strip() or None
    org.portal_welcome_message = (body.portal_welcome_message or "").strip() or None
    db.commit()
    return {
        "organization_id": org.id,
        "name": org.name,
        "portal_logo_url": org.portal_logo_url,
        "portal_welcome_message": org.portal_welcome_message,
    }


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


# ── Deadlines ────────────────────────────────────────────────────────────────

class DeadlineCreate(BaseModel):
    deadline_type: str = Field(..., pattern="^(hearing|defense|appeal|performance|payment|expiry|custom)$")
    deadline_at: datetime
    timezone: str = "Asia/Shanghai"
    owner_id: int
    description: Optional[str] = None
    reminder_offsets_json: Optional[str] = None  # JSON array, e.g. "[7,3,1]"
    is_historical: int = 0


@router.post("/orgs/{org_id}/cases/{case_id}/deadlines")
def create_deadline(
    org_id: int,
    case_id: int,
    body: DeadlineCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_case_manager(db, current_user.id, org_id, case_id)
    _require_organization_member(db, org_id, body.owner_id)
    deadline = LegalDeadline(
        organization_id=org_id,
        case_id=case_id,
        deadline_type=body.deadline_type,
        deadline_at=body.deadline_at,
        timezone=body.timezone,
        owner_id=body.owner_id,
        description=body.description,
        reminder_offsets_json=body.reminder_offsets_json or "[7,3,1]",
        is_historical=body.is_historical,
        created_by=current_user.id,
    )
    db.add(deadline)
    db.commit()
    db.refresh(deadline)
    return deadline


class DeadlinePatch(BaseModel):
    action: Optional[str] = None  # complete / cancel
    deadline_at: Optional[datetime] = None
    owner_id: Optional[int] = None
    description: Optional[str] = None
    reminder_offsets_json: Optional[str] = None


@router.patch("/deadlines/{deadline_id}")
def patch_deadline(
    deadline_id: int,
    body: DeadlinePatch,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    dl = db.query(LegalDeadline).filter(LegalDeadline.id == deadline_id).first()
    if not dl:
        raise HTTPException(404)
    if not dl.case_id:
        raise HTTPException(404)
    _require_case_manager(db, current_user.id, dl.organization_id, dl.case_id)
    if body.action == "complete":
        dl.status = "completed"
    elif body.action == "cancel":
        dl.status = "cancelled"
    if body.deadline_at:
        dl.deadline_at = body.deadline_at
    if body.owner_id is not None:
        _require_organization_member(db, dl.organization_id, body.owner_id)
        dl.owner_id = body.owner_id
    if body.description is not None:
        dl.description = body.description
    if body.reminder_offsets_json is not None:
        dl.reminder_offsets_json = body.reminder_offsets_json
    db.commit()
    db.refresh(dl)
    return dl


@router.post("/deadlines/{deadline_id}/calendar-suggestion")
def deadline_to_calendar(
    deadline_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """将关键日期转为 CalendarSuggestion，复用现有 ICS 下载路径。"""
    from app.models.calendar import CalendarSuggestion
    from datetime import timedelta

    dl = db.query(LegalDeadline).filter(LegalDeadline.id == deadline_id).first()
    if not dl:
        raise HTTPException(404)
    if not dl.case_id:
        raise HTTPException(404)
    _require_case_manager(db, current_user.id, dl.organization_id, dl.case_id)

    type_labels = {
        "hearing": "开庭", "defense": "答辩", "appeal": "上诉",
        "performance": "履行", "payment": "付款", "expiry": "到期", "custom": "自定义",
    }
    label = type_labels.get(dl.deadline_type, dl.deadline_type)

    suggestion = CalendarSuggestion(
        user_id=current_user.id,
        title=f"[{label}] {dl.description or '案件关键日期'}",
        description=f"案件关键日期 - 类型：{label}\n备注：{dl.description or ''}",
        starts_at=dl.deadline_at,
        ends_at=dl.deadline_at + timedelta(hours=1),
        attendees="[]",
        status="pending",
    )
    db.add(suggestion)
    db.commit()
    db.refresh(suggestion)
    return {"suggestion_id": suggestion.id, "title": suggestion.title,
            "starts_at": suggestion.starts_at}


@router.get("/orgs/{org_id}/cases/{case_id}/deadlines")
def list_deadlines(
    org_id: int,
    case_id: int,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_case_manager(db, current_user.id, org_id, case_id)
    q = db.query(LegalDeadline).filter(
        LegalDeadline.organization_id == org_id,
        LegalDeadline.case_id == case_id,
    )
    if status:
        q = q.filter(LegalDeadline.status == status)
    total = q.count()
    items = q.order_by(LegalDeadline.deadline_at).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# ── Portal Links ──────────────────────────────────────────────────────────────

_ALLOWED_EXPIRES_DAYS = {7, 30, 90}
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class PortalLinkCreate(BaseModel):
    client_email: str = Field(..., max_length=256, description="客户邮箱，必填，用于 OTP 验证")
    expires_days: int = Field(default=30, description="链接有效天数，只允许 7/30/90，默认 30（#93）")
    max_access_count: Optional[int] = Field(None, ge=1)
    require_email_verification: int = Field(1, ge=0, le=1)
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


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@router.post("/orgs/{org_id}/cases/{case_id}/portal-links")
def create_portal_link(
    org_id: int,
    case_id: int,
    body: PortalLinkCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from datetime import timedelta
    from app.services import security_audit_service

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
    from app.services import security_audit_service

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


# 门户公开端点（无需登录，仅校验令牌）
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
    from app.services.outbound_email_service import outbound_email_service
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
        "organization": {
            "name": org.name if org else None,
            "portal_logo_url": org.portal_logo_url if org else None,
            "portal_welcome_message": org.portal_welcome_message if org else None,
        },
    }


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

    item = db.query(LegalPortalLinkItem).filter(
        LegalPortalLinkItem.portal_link_id == link.id,
        LegalPortalLinkItem.item_type == "document",
        LegalPortalLinkItem.item_id == document_id,
    ).first()
    if not item:
        raise HTTPException(404, detail=err(PORTAL_LINK_UNAVAILABLE))

    from app.models.document import Document
    from app.services.document_delivery_service import DocumentDeliveryError, document_delivery_service
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


# ── Case Members ──────────────────────────────────────────────────────────────

class CaseMemberCreate(BaseModel):
    user_id: int
    case_role: str = Field(..., pattern="^(owner|collaborator|viewer|client_contact)$")


@router.get("/orgs/{org_id}/cases/{case_id}/members")
def list_case_members(
    org_id: int,
    case_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_case_manager(db, current_user.id, org_id, case_id)
    members = db.query(LegalCaseMember).filter(
        LegalCaseMember.case_id == case_id,
        LegalCaseMember.organization_id == org_id,
        LegalCaseMember.revoked_at.is_(None),
    ).all()
    return members


@router.post("/orgs/{org_id}/cases/{case_id}/members")
def add_case_member(
    org_id: int,
    case_id: int,
    body: CaseMemberCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_case_manager(db, current_user.id, org_id, case_id)
    _require_organization_member(db, org_id, body.user_id)
    existing = db.query(LegalCaseMember).filter(
        LegalCaseMember.case_id == case_id,
        LegalCaseMember.organization_id == org_id,
        LegalCaseMember.user_id == body.user_id,
        LegalCaseMember.revoked_at.is_(None),
    ).first()
    if existing:
        raise HTTPException(409, detail="该成员已在案件中")

    member = LegalCaseMember(
        case_id=case_id,
        organization_id=org_id,
        user_id=body.user_id,
        case_role=body.case_role,
        granted_by=current_user.id,
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    return member


@router.patch("/case-members/{member_id}")
def patch_case_member(
    member_id: int,
    case_role: Optional[str] = None,
    revoke: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    member = db.query(LegalCaseMember).filter(LegalCaseMember.id == member_id).first()
    if not member:
        raise HTTPException(404)
    _require_case_manager(db, current_user.id, member.organization_id, member.case_id)
    if revoke:
        member.revoked_at = datetime.now(timezone.utc)
    if case_role:
        member.case_role = case_role
    db.commit()
    db.refresh(member)
    return member


# ── Progress Updates ──────────────────────────────────────────────────────────

class ProgressUpdateCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=128)
    body: str = Field(..., min_length=1, max_length=5000)
    next_steps: Optional[str] = Field(None, max_length=1000)
    visibility: str = Field("internal", pattern="^(internal|client_visible)$")


@router.post("/orgs/{org_id}/cases/{case_id}/progress-updates")
def create_progress_update(
    org_id: int,
    case_id: int,
    body: ProgressUpdateCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    case = _require_progress_editor(db, current_user.id, org_id, case_id)
    update = LegalCaseProgressUpdate(
        case_id=case_id,
        organization_id=case.organization_id,
        title=body.title,
        body=body.body,
        next_steps=body.next_steps,
        visibility=body.visibility,
        # 对客户可见内容一律走审核；内部记录可保留草稿。
        status="pending_review" if body.visibility == "client_visible" else "draft",
        created_by=current_user.id,
    )
    db.add(update)
    db.commit()
    db.refresh(update)
    return update


@router.post("/progress-updates/{update_id}/publish")
def publish_progress_update(
    update_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    update = db.query(LegalCaseProgressUpdate).filter(
        LegalCaseProgressUpdate.id == update_id
    ).first()
    if not update:
        raise HTTPException(404)
    member = _require_organization_member(db, update.organization_id, current_user.id)
    if member.legal_role not in ("admin", "reviewer"):
        raise HTTPException(403, detail="仅审核律师或管理员可发布客户进度")
    if update.visibility != "client_visible" or update.status != "pending_review":
        raise HTTPException(409, detail=f"Cannot publish a {update.status} update")
    update.status = "published"
    update.published_at = datetime.now(timezone.utc)
    update.reviewed_by = current_user.id
    db.commit()
    db.refresh(update)
    return update


@router.post("/progress-updates/{update_id}/withdraw")
def withdraw_progress_update(
    update_id: int,
    reason: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    update = db.query(LegalCaseProgressUpdate).filter(
        LegalCaseProgressUpdate.id == update_id
    ).first()
    if not update:
        raise HTTPException(404)
    member = _require_organization_member(db, update.organization_id, current_user.id)
    if member.legal_role not in ("admin", "reviewer"):
        raise HTTPException(403, detail="仅审核律师或管理员可撤回客户进度")
    if update.status != "published":
        raise HTTPException(409, detail="只能撤回已发布的更新")
    update.status = "withdrawn"
    update.withdrawn_at = datetime.now(timezone.utc)
    update.withdraw_reason = reason
    db.commit()
    db.refresh(update)
    return update


@router.get("/orgs/{org_id}/cases/{case_id}/progress-updates")
def list_progress_updates(
    org_id: int,
    case_id: int,
    visibility: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_case_manager(db, current_user.id, org_id, case_id)
    q = db.query(LegalCaseProgressUpdate).filter(
        LegalCaseProgressUpdate.case_id == case_id,
        LegalCaseProgressUpdate.organization_id == org_id,
    )
    if visibility:
        q = q.filter(LegalCaseProgressUpdate.visibility == visibility)
    total = q.count()
    items = q.order_by(LegalCaseProgressUpdate.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}
