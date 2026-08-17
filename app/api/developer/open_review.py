"""开放平台 Open API 子路由：外部调用方合同审查任务提交/查询/取消。"""
import hashlib
import ipaddress
import json
import time
from datetime import datetime, timezone
from typing import Optional

import redis as redis_lib
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.error_codes import (
    API_KEY_INVALID,
    API_KEY_IP_DENIED,
    IDEMPOTENCY_KEY_CONFLICT,
    QUOTA_EXCEEDED,
    UNAUTHORIZED,
    err,
)
from app.models.legal_platform import (
    DeveloperApiKey,
    DeveloperApiUsage,
    DeveloperApp,
    LegalAsyncJob,
    LegalAsyncJobInput,
)
from app.models.org import OrganizationMember
from app.services.jobs.idempotency_service import IdempotencyConflictError, idempotency_service

open_router = APIRouter()

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
    from app.models.subscription import SubscriptionPlan, UserSubscription

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


def _open_review_idempotency_scope(app_id: int) -> str:
    """A caller key is meaningful only within the authenticated developer app."""
    return f"open_api.contract_review:{app_id}"


def _open_review_job_key(app_id: int, key: str) -> str:
    """Keep the legacy global job-key uniqueness while isolating caller keys per app."""
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"open-review:{app_id}:{digest}"


@open_router.post("/v1/contract-reviews", status_code=202)
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

    # Caller-provided idempotency keys must not cross developer-app boundaries.
    # LegalAsyncJob has a legacy global unique column, so persist a scoped digest.
    ik_scope = _open_review_idempotency_scope(app.id)
    ik_key = body.idempotency_key
    job_ik_key = _open_review_job_key(app.id, ik_key) if ik_key else None
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
        existing = (
            db.query(LegalAsyncJob)
            .join(LegalAsyncJobInput, LegalAsyncJobInput.job_id == LegalAsyncJob.id)
            .filter(
                LegalAsyncJob.idempotency_key.in_([job_ik_key, ik_key]),
                LegalAsyncJobInput.app_id == app.id,
            )
            .first()
        )
        if existing:
            stored = db.query(LegalAsyncJobInput).filter(LegalAsyncJobInput.job_id == existing.id).first()
            if stored and stored.request_fingerprint != fingerprint:
                idempotency_service.fail(db, scope=ik_scope, key=ik_key)
                raise HTTPException(409, detail=err(IDEMPOTENCY_KEY_CONFLICT))
            from app.services.jobs.async_job_service import serialize_job
            snapshot = serialize_job(existing, open_api=True)
            idempotency_service.complete(db, scope=ik_scope, key=ik_key, response_snapshot=snapshot)
            return {**snapshot, "idempotent": True}

    try:
        job = LegalAsyncJob(
            organization_id=app.organization_id,
            job_type="open_contract_review",
            status="queued",
            idempotency_key=job_ik_key,
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

        # Contract review is billable. Do not accept a task unless the atomic
        # quota reservation succeeds; a failed reservation leaves no job behind.
        from app.services.billing.subscription_service import subscription_service

        subscription_service.ensure_default_plans(db)
        admin_member = db.query(OrganizationMember).filter(
            OrganizationMember.organization_id == app.organization_id,
            OrganizationMember.legal_role == "admin",
        ).first()
        if admin_member is None:
            db.query(LegalAsyncJobInput).filter(LegalAsyncJobInput.job_id == job.id).delete()
            db.delete(job)
            db.commit()
            raise HTTPException(503, detail="组织未配置配额管理员")
        quota_result = subscription_service.try_consume_quota(
            db=db, user_id=admin_member.user_id, quota_type="review",
            usage_event_id=f"open-review:{job.id}",
            source_type="open_contract_review", source_id=str(job.id))
        if not quota_result["ok"]:
            db.query(LegalAsyncJobInput).filter(LegalAsyncJobInput.job_id == job.id).delete()
            db.delete(job)
            db.commit()
            raise HTTPException(429, detail=err(QUOTA_EXCEEDED))

        duration_ms = int((time.monotonic() - started) * 1000)
        db.add(DeveloperApiUsage(app_id=app.id, organization_id=app.organization_id,
            endpoint="/v1/contract-reviews", method="POST", status_code=202,
            duration_ms=duration_ms, stat_date=datetime.now(timezone.utc).date().isoformat(),
            stat_hour=datetime.now(timezone.utc).hour))
        db.commit()
        try:
            from app.core.obs_context import get_context
            from app.tasks import process_open_contract_review_task
            process_open_contract_review_task.delay(job.id, headers=get_context().as_headers())
        except Exception:
            # Worker unavailable does not lose the queued task; beat can consume it later.
            pass
    except Exception:
        if ik_key:
            db.rollback()
            idempotency_service.fail(db, scope=ik_scope, key=ik_key)
        raise

    if ik_key:
        from app.services.jobs.async_job_service import serialize_job
        idempotency_service.complete(
            db, scope=ik_scope, key=ik_key,
            response_snapshot=serialize_job(job, open_api=True),
            resource_id=job.id,
        )
    from app.services.jobs.async_job_service import serialize_job
    return serialize_job(job, open_api=True)


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
    from app.services.jobs.async_job_service import serialize_job
    return serialize_job(job, open_api=True)


@open_router.post("/v1/tasks/{task_id}/cancel")
def open_cancel_task(
    task_id: int,
    x_api_key: str = Header(None),
    db: Session = Depends(get_db),
):
    """取消异步任务（幂等：已终态返回当前状态）。取消行为写安全审计。"""
    api_key, app = _authenticate_api_key(x_api_key, db)
    job = db.query(LegalAsyncJob).filter(
        LegalAsyncJob.id == task_id,
        LegalAsyncJob.organization_id == app.organization_id,
    ).first()
    if not job:
        raise HTTPException(404)
    from app.services.jobs.async_job_service import cancel_job
    return cancel_job(
        db,
        job=job,
        actor_type="api_key",
        actor_id=f"app:{app.id}",
        reason_code="open_api_cancel",
    )

