"""Phase 13/14 — 开放平台 / 安全审计 / 异步任务 / 通知偏好 API"""

import hashlib
import ipaddress
import json
import secrets
import time
from datetime import datetime, timezone
from typing import Optional

import redis as redis_lib
from fastapi import APIRouter, Depends, HTTPException, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.auth import get_current_user, verify_org_role_access
from app.core.error_codes import err, API_KEY_INVALID, API_KEY_IP_DENIED, IDEMPOTENCY_KEY_CONFLICT, UNAUTHORIZED
from app.services.idempotency_service import IdempotencyConflictError, idempotency_service
from app.models.user import User
from app.models.org import OrganizationMember, LegalMemberRole
from app.models.legal_platform import (
    DeveloperApp, DeveloperApiKey, DeveloperApiUsage,
    WebhookSubscription, WebhookDelivery, LegalAsyncJob,
    LegalAsyncJobInput,
)
from app.models.legal_notifications import (
    SecurityAuditEvent, LegalNotificationPreference,
    LegalNotificationPolicy, LegalNotificationEvent,
    OrganizationOnboardingProgress,
)

router = APIRouter()
open_router = APIRouter()


# ── Developer Apps ────────────────────────────────────────────────────────────

class AppCreate(BaseModel):
    name: str = Field(..., max_length=128)
    ip_whitelist_json: Optional[str] = None
    webhook_url: Optional[str] = None


def _gen_open_api_key() -> tuple[str, str, str]:
    raw = "lzj_op_" + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    # 列宽 String(12)，与 api_key_api 一致；超过 12 会触发 MySQL 1406
    key_prefix = raw[:12]
    return raw, key_hash, key_prefix


@router.post("/orgs/{org_id}/apps")
def create_developer_app(
    org_id: int,
    body: AppCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    member = verify_org_role_access(org_id, current_user.id, LegalMemberRole.admin, db)
    from app.services.security_audit_service import write_event

    existing = db.query(DeveloperApp).filter(
        DeveloperApp.organization_id == org_id,
        DeveloperApp.name == body.name,
    ).first()
    if existing:
        raise HTTPException(409, detail="应用名称在本组织内已存在")

    app = DeveloperApp(
        organization_id=org_id,
        name=body.name,
        ip_whitelist_json=body.ip_whitelist_json,
        webhook_url=body.webhook_url,
        created_by=member.user_id,
    )
    db.add(app)
    db.flush()

    raw_key, key_hash, key_prefix = _gen_open_api_key()
    api_key = DeveloperApiKey(
        app_id=app.id,
        organization_id=org_id,
        key_hash=key_hash,
        key_prefix=key_prefix,
    )
    db.add(api_key)
    db.commit()
    db.refresh(app)

    write_event(
        event_type="key_op", actor_type="user", actor_id=str(member.user_id),
        result="success", organization_id=org_id,
        target_type="developer_app", target_id=str(app.id), db=db,
    )
    return {"app": app, "api_key": raw_key, "key_prefix": key_prefix}


@router.get("/orgs/{org_id}/apps")
def list_developer_apps(
    org_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    verify_org_role_access(org_id, current_user.id, LegalMemberRole.admin, db)
    return db.query(DeveloperApp).filter(
        DeveloperApp.organization_id == org_id
    ).all()


@router.post("/orgs/{org_id}/apps/{app_id}/keys/rotate")
def rotate_api_key(
    org_id: int,
    app_id: int,
    transition_hours: int = 24,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    member = verify_org_role_access(org_id, current_user.id, LegalMemberRole.admin, db)
    from app.services.security_audit_service import write_event
    app = db.query(DeveloperApp).filter(
        DeveloperApp.id == app_id,
        DeveloperApp.organization_id == org_id
    ).first()
    if not app:
        raise HTTPException(404)

    from datetime import timedelta
    now = datetime.now(timezone.utc)

    old_keys = db.query(DeveloperApiKey).filter(
        DeveloperApiKey.app_id == app_id,
        DeveloperApiKey.status == "active",
    ).all()
    for old_key in old_keys:
        old_key.transition_until = now + timedelta(hours=transition_hours)

    raw_key, key_hash, key_prefix = _gen_open_api_key()
    new_key = DeveloperApiKey(
        app_id=app_id,
        organization_id=app.organization_id,
        key_hash=key_hash,
        key_prefix=key_prefix,
    )
    db.add(new_key)
    db.commit()

    write_event(
        event_type="key_op", actor_type="user", actor_id=str(member.user_id),
        result="success", organization_id=org_id,
        target_type="developer_api_key", target_id=f"app:{app_id}",
        db=db,
    )
    return {"new_api_key": raw_key, "key_prefix": key_prefix, "transition_hours": transition_hours}


@router.post("/orgs/{org_id}/apps/{app_id}/webhooks/test")
def test_webhook(
    org_id: int,
    app_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    verify_org_role_access(org_id, current_user.id, LegalMemberRole.admin, db)
    app = db.query(DeveloperApp).filter(
        DeveloperApp.id == app_id,
        DeveloperApp.organization_id == org_id
    ).first()
    if not app or not app.webhook_url:
        raise HTTPException(400, detail="应用不存在或未配置 Webhook 地址")

    delivery = WebhookDelivery(
        subscription_id=0,
        app_id=app_id,
        organization_id=org_id,
        event_type="test.ping",
        event_id=f"test_{secrets.token_hex(8)}",
        status="pending",
    )
    db.add(delivery)
    db.commit()
    db.refresh(delivery)
    return {"delivery_id": delivery.id, "status": "queued", "webhook_url": app.webhook_url}


# ── API Usage ─────────────────────────────────────────────────────────────────

@router.get("/orgs/{org_id}/apps/{app_id}/usage")
def get_api_usage(
    org_id: int,
    app_id: int,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    verify_org_role_access(org_id, current_user.id, LegalMemberRole.admin, db)
    # 验证app归属
    app = db.query(DeveloperApp).filter(
        DeveloperApp.id == app_id,
        DeveloperApp.organization_id == org_id
    ).first()
    if not app:
        raise HTTPException(404, detail="应用不存在")

    q = db.query(DeveloperApiUsage).filter(DeveloperApiUsage.app_id == app_id)
    if start_date:
        q = q.filter(DeveloperApiUsage.stat_date >= start_date)
    if end_date:
        q = q.filter(DeveloperApiUsage.stat_date <= end_date)
    total = q.count()
    items = q.order_by(DeveloperApiUsage.stat_date.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# ── Open API (外部调用方) ─────────────────────────────────────────────────────

class OpenContractReviewRequest(BaseModel):
    title: str = Field(..., max_length=256)
    content: str = Field(..., min_length=10)
    contract_type: Optional[str] = None
    review_policy_id: Optional[int] = None
    idempotency_key: Optional[str] = None


def _authenticate_api_key(x_api_key: str, db: Session, request_ip: Optional[str] = None):
    if not x_api_key:
        raise HTTPException(401, detail=err(UNAUTHORIZED))
    key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    api_key = db.query(DeveloperApiKey).filter(
        DeveloperApiKey.key_hash == key_hash,
        DeveloperApiKey.status == "active",
    ).first()
    if not api_key:
        raise HTTPException(403, detail=err(API_KEY_INVALID))
    app = db.query(DeveloperApp).filter(
        DeveloperApp.id == api_key.app_id,
        DeveloperApp.status == "active",
    ).first()
    if not app:
        raise HTTPException(403, detail=err(API_KEY_INVALID))

    # IP 白名单校验
    if app.ip_whitelist_json and request_ip:
        try:
            whitelist = json.loads(app.ip_whitelist_json)
        except (ValueError, TypeError):
            whitelist = []
        if whitelist:
            try:
                req_addr = ipaddress.ip_address(request_ip)
                in_list = any(
                    req_addr in ipaddress.ip_network(cidr, strict=False)
                    for cidr in whitelist
                )
            except ValueError:
                in_list = False
            if not in_list:
                raise HTTPException(403, detail=err(API_KEY_IP_DENIED))

    # 更新最近使用时间（非阻塞，失败不影响请求）
    try:
        api_key.last_used_at = datetime.now(timezone.utc)
        db.commit()
    except Exception:
        pass

    return api_key, app


_RATE_LIMITS = {
    "free": 100,
    "pro": 1000,
    "team": 5000,
    "enterprise": 20000,
}


def _get_org_plan_tier(org_id: int, db: Session) -> str:
    """通过组织管理员的订阅查找套餐层级，无订阅时返回 'free'。"""
    from app.models.subscription import UserSubscription, SubscriptionPlan

    admin_member = db.query(OrganizationMember).filter(
        OrganizationMember.organization_id == org_id,
        OrganizationMember.legal_role == "admin",
    ).first()
    if not admin_member:
        return "free"

    sub = db.query(UserSubscription).filter(
        UserSubscription.user_id == admin_member.user_id,
        UserSubscription.status == "active",
    ).first()
    if not sub:
        return "free"

    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == sub.plan_id).first()
    return plan.tier if plan else "free"


def _check_rate_limit(app_obj: "DeveloperApp", db: Session) -> None:
    """按组织订阅套餐检查每日 Open API 调用限额。超限抛 429。"""
    from datetime import date

    plan_tier = _get_org_plan_tier(app_obj.organization_id, db)
    limit = _RATE_LIMITS.get(plan_tier)
    if limit is None:
        return  # 无限

    today = date.today().isoformat()
    key = f"api_ratelimit:{app_obj.id}:{today}"
    r = redis_lib.from_url(get_settings().REDIS_URL, decode_responses=True)
    count = r.incr(key)
    if count == 1:
        r.expire(key, 86400)
    if count > limit:
        from app.core.error_codes import API_RATE_LIMIT_EXCEEDED
        raise HTTPException(429, detail=err(API_RATE_LIMIT_EXCEEDED),
                            headers={"Retry-After": "86400"})


@open_router.post("/v1/contract-reviews")
def open_create_contract_review(
    body: OpenContractReviewRequest,
    request: Request,
    x_api_key: str = Header(None),
    db: Session = Depends(get_db),
):
    if not get_settings().OPEN_API_ENABLED:
        raise HTTPException(503, "开放平台异步任务服务未上线（P0-05），暂不接受任务提交")
    started = time.monotonic()
    request_ip = request.client.host if request.client else None
    api_key, app = _authenticate_api_key(x_api_key, db, request_ip)
    _check_rate_limit(app, db)

    fingerprint = hashlib.sha256(json.dumps({
        "title": body.title, "content": body.content, "contract_type": body.contract_type,
        "review_policy_id": body.review_policy_id,
    }, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

    # 通用幂等键：DB 唯一约束兜底并发（scope+key 唯一），失败可重试、过期可清理。
    ik_scope = "open_api.contract_review"
    ik_key = body.idempotency_key
    if ik_key:
        try:
            ik = idempotency_service.begin(db, scope=ik_scope, key=ik_key, request_hash=fingerprint)
        except IdempotencyConflictError as exc:
            raise HTTPException(409, detail=err(exc.code))
        if ik["replay"]:
            snapshot = ik["response_snapshot"]
            if snapshot:
                return {**json.loads(snapshot), "idempotent": True}
        # 兼容历史请求：本机制上线前已用同 key 创建的异步任务
        existing = db.query(LegalAsyncJob).filter(
            LegalAsyncJob.idempotency_key == ik_key
        ).first()
        if existing:
            stored = db.query(LegalAsyncJobInput).filter(LegalAsyncJobInput.job_id == existing.id).first()
            if stored and stored.request_fingerprint != fingerprint:
                idempotency_service.fail(db, scope=ik_scope, key=ik_key)
                raise HTTPException(409, detail=err(IDEMPOTENCY_KEY_CONFLICT))
            snapshot = {"task_id": existing.id, "status": existing.status}
            idempotency_service.complete(db, scope=ik_scope, key=ik_key, response_snapshot=snapshot)
            return {"task_id": existing.id, "status": existing.status, "idempotent": True}

    try:
        job = LegalAsyncJob(
            organization_id=app.organization_id,
            job_type="open_contract_review",
            status="queued",
            idempotency_key=ik_key,
            created_by=0,
        )
        db.add(job)
        db.flush()
        db.add(LegalAsyncJobInput(
            job_id=job.id, app_id=app.id, request_fingerprint=fingerprint, title=body.title,
            content_ciphertext=body.content, contract_type=body.contract_type,
            review_policy_id=body.review_policy_id,
        ))
        db.commit()
        db.refresh(job)

        # 联动扣减组织管理员的月度审查配额（失败不阻断接口）
        try:
            from app.models.org import OrganizationMember
            from app.services.subscription_service import subscription_service
            admin_member = db.query(OrganizationMember).filter(
                OrganizationMember.organization_id == app.organization_id,
                OrganizationMember.legal_role == "admin",
            ).first()
            if admin_member:
                subscription_service.record_usage(db, admin_member.user_id, "review")
        except Exception:
            pass

        duration_ms = int((time.monotonic() - started) * 1000)
        db.add(DeveloperApiUsage(app_id=app.id, organization_id=app.organization_id,
            endpoint="/v1/contract-reviews", method="POST", status_code=202,
            duration_ms=duration_ms, stat_date=datetime.now(timezone.utc).date().isoformat(),
            stat_hour=datetime.now(timezone.utc).hour))
        db.commit()
        try:
            from app.tasks import process_open_contract_review_task
            process_open_contract_review_task.delay(job.id)
        except Exception:
            # Worker unavailable does not lose the queued task; beat can consume it later.
            pass
    except Exception:
        if ik_key:
            db.rollback()
            idempotency_service.fail(db, scope=ik_scope, key=ik_key)
        raise

    if ik_key:
        idempotency_service.complete(
            db, scope=ik_scope, key=ik_key,
            response_snapshot={"task_id": job.id, "status": job.status},
        )
    return {"task_id": job.id, "status": job.status}


@open_router.get("/v1/tasks/{task_id}")
def open_get_task(
    task_id: int,
    x_api_key: str = Header(None),
    db: Session = Depends(get_db),
):
    api_key, app = _authenticate_api_key(x_api_key, db)
    job = db.query(LegalAsyncJob).filter(
        LegalAsyncJob.id == task_id,
        LegalAsyncJob.organization_id == app.organization_id,
    ).first()
    if not job:
        raise HTTPException(404)
    return {
        "task_id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "progress": job.progress,
        "result_summary": job.result_summary,
        "error_summary": job.error_summary,
        "created_at": job.created_at,
    }


# ── Legal Async Jobs ──────────────────────────────────────────────────────────

@router.get("/orgs/{org_id}/async-jobs")
def list_async_jobs(
    org_id: int,
    job_type: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    verify_org_role_access(org_id, current_user.id, LegalMemberRole.admin, db)
    q = db.query(LegalAsyncJob).filter(LegalAsyncJob.organization_id == org_id)
    if job_type:
        q = q.filter(LegalAsyncJob.job_type == job_type)
    if status:
        q = q.filter(LegalAsyncJob.status == status)
    total = q.count()
    items = q.order_by(LegalAsyncJob.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/orgs/{org_id}/operations/summary")
def get_operations_summary(
    org_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """管理员运营看板的最小可信指标，全部来自持久化记录。"""
    verify_org_role_access(org_id, current_user.id, LegalMemberRole.admin, db)
    from sqlalchemy import func
    jobs = db.query(LegalAsyncJob).filter(LegalAsyncJob.organization_id == org_id)
    deliveries = db.query(WebhookDelivery).filter(WebhookDelivery.organization_id == org_id)
    usage = db.query(DeveloperApiUsage).filter(DeveloperApiUsage.organization_id == org_id)
    return {
        "queued_jobs": jobs.filter(LegalAsyncJob.status == "queued").count(),
        "failed_jobs": jobs.filter(LegalAsyncJob.status == "failed").count(),
        "failed_webhooks": deliveries.filter(WebhookDelivery.status == "failed").count(),
        "pending_webhooks": deliveries.filter(WebhookDelivery.status == "pending").count(),
        "api_calls": usage.with_entities(func.coalesce(func.sum(DeveloperApiUsage.call_count), 0)).scalar(),
        "callback_verification_failures": db.query(SecurityAuditEvent).filter(
            SecurityAuditEvent.organization_id == org_id,
            SecurityAuditEvent.event_type == "sign_callback",
            SecurityAuditEvent.result != "success",
        ).count(),
    }


# ── Security Audit Events ─────────────────────────────────────────────────────

@router.get("/orgs/{org_id}/security-audit")
def list_audit_events(
    org_id: int,
    event_type: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    verify_org_role_access(org_id, current_user.id, LegalMemberRole.admin, db)
    q = db.query(SecurityAuditEvent).filter(SecurityAuditEvent.organization_id == org_id)
    if event_type:
        q = q.filter(SecurityAuditEvent.event_type == event_type)
    if start:
        q = q.filter(SecurityAuditEvent.occurred_at >= start)
    if end:
        q = q.filter(SecurityAuditEvent.occurred_at <= end)
    total = q.count()
    items = q.order_by(SecurityAuditEvent.occurred_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/orgs/{org_id}/security-audit/export")
def export_audit_events(
    org_id: int,
    event_type: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    fmt: str = "json",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """同步导出（≤1000 条）或创建异步任务（>1000 条）。导出行为本身写入安全审计。"""
    member = verify_org_role_access(org_id, current_user.id, LegalMemberRole.admin, db)
    from app.services.security_audit_service import write_event
    from app.services.security_audit_service import verify_chain
    from app.models.legal_platform import LegalAsyncJob

    integrity = verify_chain(organization_id=org_id)
    if not integrity["intact"]:
        write_event(event_type="export", actor_type="user", actor_id=str(member.user_id),
                    result="blocked", organization_id=org_id, target_type="security_audit_events",
                    target_id="integrity_failure", db=db)
        raise HTTPException(423, detail="安全审计链校验失败，导出已冻结")

    q = db.query(SecurityAuditEvent).filter(SecurityAuditEvent.organization_id == org_id)
    if event_type:
        q = q.filter(SecurityAuditEvent.event_type == event_type)
    if start:
        q = q.filter(SecurityAuditEvent.occurred_at >= start)
    if end:
        q = q.filter(SecurityAuditEvent.occurred_at <= end)
    total = q.count()

    write_event(
        event_type="export", actor_type="user", actor_id=str(member.user_id),
        result="success", organization_id=org_id, target_type="security_audit_events",
        target_id=f"count:{total}", db=db,
    )

    if total > 1000:
        from app.core.error_codes import err, EXPORT_ASYNC_REQUIRED
        job = LegalAsyncJob(
            organization_id=org_id, job_type="audit_export", status="queued",
            created_by=member.user_id,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return {"task_id": job.id, "status": "queued",
                "message": err(EXPORT_ASYNC_REQUIRED)["message"]}

    events = q.order_by(SecurityAuditEvent.occurred_at).all()
    rows = [
        {"seq_no": e.seq_no, "event_type": e.event_type, "actor_type": e.actor_type,
         "actor_id": e.actor_id, "result": e.result,
         "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None}
        for e in events
    ]
    if fmt == "csv":
        import csv, io
        buf = io.StringIO()
        if rows:
            writer = csv.DictWriter(buf, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                                 headers={"Content-Disposition": "attachment; filename=audit.csv"})
    return {"total": total, "events": rows}


@router.get("/orgs/{org_id}/security-audit/integrity-check")
def audit_integrity_check(
    org_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """管理员触发哈希链完整性校验。"""
    verify_org_role_access(org_id, current_user.id, LegalMemberRole.admin, db)
    from app.services.security_audit_service import verify_chain
    result = verify_chain(organization_id=org_id)
    return result


# ── Notification Preferences ──────────────────────────────────────────────────

class NotificationPrefUpdate(BaseModel):
    channels_json: Optional[str] = None
    mute_start: Optional[str] = Field(None, pattern=r"^\d{2}:\d{2}$")
    mute_end: Optional[str] = Field(None, pattern=r"^\d{2}:\d{2}$")
    timezone: Optional[str] = None
    delegate_user_id: Optional[int] = None
    summary_frequency: Optional[str] = None


@router.get("/notification-preferences/me")
def get_notification_preferences(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    prefs = db.query(LegalNotificationPreference).filter(
        LegalNotificationPreference.user_id == current_user.id,
    ).all()
    return prefs


@router.put("/notification-preferences/me")
def update_notification_preferences(
    event_type: str,
    body: NotificationPrefUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    pref = db.query(LegalNotificationPreference).filter(
        LegalNotificationPreference.user_id == current_user.id,
        LegalNotificationPreference.event_type == event_type,
    ).first()

    if not pref:
        pref = LegalNotificationPreference(
            user_id=current_user.id,
            organization_id=current_user.organization_id or 0,
            event_type=event_type,
        )
        db.add(pref)

    for field, value in body.dict(exclude_none=True).items():
        setattr(pref, field, value)

    db.commit()
    db.refresh(pref)
    return pref


@router.put("/cases/{case_id}/notification-policy")
def update_notification_policy(
    case_id: int,
    event_type: str,
    advance_days_json: Optional[str] = None,
    escalation_user_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 验证案件访问权限
    from app.models.legal import LegalCase
    case = db.query(LegalCase).filter(LegalCase.id == case_id).first()
    if not case:
        raise HTTPException(404, detail="案件不存在")

    # 案件策略影响客户与律师提醒，仅 reviewer/admin 可修改。
    from app.services.org_service import org_service
    member = org_service.get_user_org_member(
        db=db,
        user_id=current_user.id,
        org_id=case.organization_id
    )
    if not member:
        raise HTTPException(404, detail="案件不存在")
    if member.legal_role not in ("admin", "reviewer"):
        raise HTTPException(403, detail="仅审核律师或管理员可修改案件通知策略")

    if advance_days_json is not None:
        try:
            offsets = json.loads(advance_days_json)
        except (TypeError, ValueError):
            raise HTTPException(422, detail="advance_days_json 必须是正整数数组")
        if not isinstance(offsets, list) or any(not isinstance(day, int) or day < 0 or day > 365 for day in offsets):
            raise HTTPException(422, detail="advance_days_json 必须是 0-365 的整数数组")
    if escalation_user_id is not None:
        escalation_member = org_service.get_user_org_member(
            db=db, user_id=escalation_user_id, org_id=case.organization_id,
        )
        if not escalation_member:
            raise HTTPException(422, detail="升级接收人必须是案件所属组织成员")

    policy = db.query(LegalNotificationPolicy).filter(
        LegalNotificationPolicy.case_id == case_id,
        LegalNotificationPolicy.event_type == event_type,
    ).first()

    if not policy:
        policy = LegalNotificationPolicy(
            case_id=case_id,
            organization_id=case.organization_id,
            event_type=event_type,
        )
        db.add(policy)

    if advance_days_json is not None:
        policy.advance_days_json = advance_days_json
    if escalation_user_id is not None:
        policy.escalation_user_id = escalation_user_id
    policy.updated_by = current_user.id
    db.commit()
    db.refresh(policy)
    return policy


@router.post("/notifications/{notification_id}/acknowledge")
def acknowledge_notification(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    notif = db.query(LegalNotificationEvent).filter(
        LegalNotificationEvent.id == notification_id,
        LegalNotificationEvent.user_id == current_user.id,
    ).first()
    if not notif:
        raise HTTPException(404)
    from app.services.notification_service import notification_service

    notification_service.mark_acknowledged(db, notif)
    db.commit()
    db.refresh(notif)
    return notif


# ── Notification Center ───────────────────────────────────────────────────────

@router.get("/notifications/me")
def get_my_notifications(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """当前用户最近 50 条站内通知 + 未读数（delivered/sent 计为未读）。"""
    from app.services.notification_service import notification_service
    events = notification_service.get_user_notifications(
        db=db, user_id=current_user.id, limit=50,
    )
    items = [notification_service.serialize_event(e) for e in events if e.status != "failed"]
    unread = notification_service.get_unread_count(db=db, user_id=current_user.id)
    return {"items": items, "unread": unread}


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """标记单条站内通知为已读；非本人或不存在返回 404。"""
    from app.services.notification_service import notification_service
    try:
        notification_service.mark_as_read(db=db, event_id=notification_id, user_id=current_user.id)
    except ValueError:
        raise HTTPException(404, detail="通知不存在")
    return {"ok": True}


@router.post("/notifications/read-all")
def mark_all_notifications_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """标记当前用户全部站内通知为已读。"""
    from app.services.notification_service import notification_service
    updated = notification_service.mark_all_as_read(db=db, user_id=current_user.id)
    return {"ok": True, "updated": updated}


# ── Onboarding ────────────────────────────────────────────────────────────────

class OnboardingUpdate(BaseModel):
    user_role: Optional[str] = None
    completed_steps_json: Optional[str] = None
    skipped_steps_json: Optional[str] = None


@router.get("/onboarding")
def get_onboarding(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not current_user.organization_id:
        raise HTTPException(400, detail="无所属组织")
    progress = db.query(OrganizationOnboardingProgress).filter(
        OrganizationOnboardingProgress.organization_id == current_user.organization_id
    ).first()
    return progress


@router.put("/onboarding")
def update_onboarding(
    body: OnboardingUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not current_user.organization_id:
        raise HTTPException(400, detail="无所属组织")

    progress = db.query(OrganizationOnboardingProgress).filter(
        OrganizationOnboardingProgress.organization_id == current_user.organization_id
    ).first()

    if not progress:
        progress = OrganizationOnboardingProgress(
            organization_id=current_user.organization_id
        )
        db.add(progress)

    if body.user_role is not None:
        progress.user_role = body.user_role
    if body.completed_steps_json is not None:
        progress.completed_steps_json = body.completed_steps_json
    if body.skipped_steps_json is not None:
        progress.skipped_steps_json = body.skipped_steps_json

    db.commit()
    db.refresh(progress)
    return progress
