"""Outbox 投递看板（运营口径，读数据库明细，不依赖进程内指标）。

两类 Outbox 一起看：
- ``legal_notification_events``：通知 Outbox（站内 / 邮件 / 微信 / 飞书），含审批与重试；
- ``email_send_requests``：邮件 Outbox（真实 SMTP 投递），含 DLP 阻断与死信。

口径约定（避免看板与实际投递混淆）：
- ``window``：按创建时间落在统计窗口内的事件，用于算投递率与渠道分布；
- ``backlog``：**当前**未投递完成的数量（不限窗口），用于发现积压与卡死；
- 成功率 = 成功 / (成功 + 失败 + 死信)，审批中/待投递不计入分母；无样本返回 None。

本模块只读 + 两个人工重试入口（死信重排），不改变任何投递语义。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models.email import EmailSendRequest, OutboundEmailPolicy
from app.models.legal_notifications import LegalNotificationEvent, OutboxAlertState
from app.models.user import User

WINDOW_DEFAULT_DAYS = 7
WINDOW_MAX_DAYS = 90
TREND_MAX_BUCKETS = 30
DEAD_LETTER_PAGE_SIZE = 20
FAILURE_CODE_LIMIT = 10

# 通知事件的成功/失败终态（read/acknowledged 属于投递后的用户行为，仍算成功侧）。
NOTIFICATION_SUCCESS = ("sent", "delivered", "read", "acknowledged")
NOTIFICATION_FAILURE = ("failed", "dead_letter")
NOTIFICATION_BACKLOG = ("pending", "approved", "sending")

# 邮件 Outbox 的成功/失败终态。
EMAIL_SUCCESS = ("sent",)
EMAIL_FAILURE = ("failed", "dead_letter")
EMAIL_BACKLOG = ("pending", "approved", "sending")

CHANNEL_LABELS = {"site": "站内", "email": "邮件", "wechat": "微信", "feishu": "飞书"}


def _rate(success: int, failed: int, dead: int) -> float | None:
    denominator = success + failed + dead
    if denominator <= 0:
        return None
    return round(success / denominator, 4)


def _group_counts(db: Session, model, column, *filters) -> dict[str, int]:
    rows = db.query(column, func.count()).filter(*filters).group_by(column).all()
    return {str(key or "unknown"): int(count) for key, count in rows}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _age_minutes(value: datetime | None, now: datetime) -> int | None:
    if not value:
        return None
    moment = value.replace(tzinfo=None) if value.tzinfo else value
    reference = now.replace(tzinfo=None) if now.tzinfo else now
    return max(0, int((reference - moment).total_seconds() // 60))


def _mask_recipient(value: str | None) -> str | None:
    """收件人脱敏：保留域名，本地部分只留首字符（看板不是通讯录）。"""
    if not value:
        return None
    address = value.strip()
    if "@" not in address:
        return address[:2] + "***"
    local, _, domain = address.partition("@")
    head = local[:1] or "*"
    return f"{head}***@{domain}"


def _org_filter(model, organization_id: int | None) -> list:
    return [] if organization_id is None else [model.organization_id == organization_id]


def _notification_backlog(db: Session, now: datetime, organization_id: int | None = None) -> dict:
    scope = _org_filter(LegalNotificationEvent, organization_id)
    counts = _group_counts(db, LegalNotificationEvent, LegalNotificationEvent.status,
                           LegalNotificationEvent.status.in_(NOTIFICATION_BACKLOG), *scope)
    retry_pending = db.query(func.count(LegalNotificationEvent.id)).filter(
        LegalNotificationEvent.status == "failed",
        LegalNotificationEvent.next_retry_at.isnot(None),
        LegalNotificationEvent.next_retry_at <= now,
        *scope,
    ).scalar() or 0
    stale_claims = db.query(func.count(LegalNotificationEvent.id)).filter(
        LegalNotificationEvent.status == "sending",
        LegalNotificationEvent.claim_expires_at.isnot(None),
        LegalNotificationEvent.claim_expires_at < now,
        *scope,
    ).scalar() or 0
    oldest = db.query(func.min(LegalNotificationEvent.created_at)).filter(
        LegalNotificationEvent.status.in_(("pending", "approved")),
        *scope,
    ).scalar()
    return {
        "pending": counts.get("pending", 0),
        "approved": counts.get("approved", 0),
        "sending": counts.get("sending", 0),
        "retry_pending": int(retry_pending),
        "stale_claims": int(stale_claims),
        "oldest_pending_at": _iso(oldest),
        "oldest_pending_age_minutes": _age_minutes(oldest, now),
        "open_total": counts.get("pending", 0) + counts.get("approved", 0) + counts.get("sending", 0),
    }


def _notification_section(db: Session, *, since: datetime, now: datetime) -> dict:
    window_filter = (LegalNotificationEvent.created_at >= since,)
    by_status = _group_counts(db, LegalNotificationEvent, LegalNotificationEvent.status, *window_filter)
    success = sum(by_status.get(status, 0) for status in NOTIFICATION_SUCCESS)

    by_channel = []
    for channel, total in sorted(
        _group_counts(db, LegalNotificationEvent, LegalNotificationEvent.channel, *window_filter).items()
    ):
        channel_status = _group_counts(
            db, LegalNotificationEvent, LegalNotificationEvent.status,
            LegalNotificationEvent.channel == channel,
            *window_filter,
        )
        delivered = sum(channel_status.get(status, 0) for status in NOTIFICATION_SUCCESS)
        by_channel.append({
            "channel": channel,
            "label": CHANNEL_LABELS.get(channel, channel),
            "total": total,
            "delivered": delivered,
            "failed": channel_status.get("failed", 0),
            "dead_letter": channel_status.get("dead_letter", 0),
            "pending": sum(channel_status.get(status, 0) for status in NOTIFICATION_BACKLOG),
            "rate": _rate(delivered, channel_status.get("failed", 0),
                          channel_status.get("dead_letter", 0)),
        })

    dead_rows = (
        db.query(LegalNotificationEvent)
        .filter(LegalNotificationEvent.status == "dead_letter", *window_filter)
        # 通知事件没有单独的 dead_letter_at：以 updated_at 作为进入终态的时间
        .order_by(LegalNotificationEvent.updated_at.desc(), LegalNotificationEvent.id.desc())
        .limit(DEAD_LETTER_PAGE_SIZE)
        .all()
    )
    dead_codes = db.query(
        LegalNotificationEvent.error_code, func.count()
    ).filter(
        LegalNotificationEvent.status.in_(NOTIFICATION_FAILURE), *window_filter,
    ).group_by(LegalNotificationEvent.error_code).order_by(func.count().desc()).limit(FAILURE_CODE_LIMIT).all()

    return {
        "total": sum(by_status.values()),
        "by_status": by_status,
        "by_channel": by_channel,
        "rate": _rate(success, by_status.get("failed", 0), by_status.get("dead_letter", 0)),
        "success": success,
        "failed": by_status.get("failed", 0),
        "dead_letter": by_status.get("dead_letter", 0),
        "backlog": _notification_backlog(db, now),
        "dead_letters": {
            "total": by_status.get("dead_letter", 0),
            "items": [{
                "id": row.id,
                "channel": row.channel,
                "channel_label": CHANNEL_LABELS.get(row.channel, row.channel),
                "event_type": row.event_type,
                "title": row.title,
                "template_key": row.template_key,
                "attempt": row.attempt,
                "error_code": row.error_code,
                "error_message": row.sanitized_error_message,
                "reference_type": row.reference_type,
                "reference_id": row.reference_id,
                "updated_at": _iso(row.updated_at),
            } for row in dead_rows],
        },
        "failure_codes": [
            {"code": code or "unknown", "count": int(count)} for code, count in dead_codes
        ],
    }


def _email_backlog(db: Session, now: datetime, organization_id: int | None = None) -> dict:
    scope = _org_filter(EmailSendRequest, organization_id)
    counts = _group_counts(db, EmailSendRequest, EmailSendRequest.status,
                           EmailSendRequest.status.in_(EMAIL_BACKLOG), *scope)
    retry_pending = db.query(func.count(EmailSendRequest.id)).filter(
        EmailSendRequest.status == "failed",
        EmailSendRequest.next_retry_at.isnot(None),
        EmailSendRequest.next_retry_at <= now,
        *scope,
    ).scalar() or 0
    stale_claims = db.query(func.count(EmailSendRequest.id)).filter(
        EmailSendRequest.status == "sending",
        EmailSendRequest.claim_expires_at.isnot(None),
        EmailSendRequest.claim_expires_at < now,
        *scope,
    ).scalar() or 0
    oldest = db.query(func.min(EmailSendRequest.created_at)).filter(
        EmailSendRequest.status.in_(("pending", "approved")),
        *scope,
    ).scalar()
    return {
        "pending": counts.get("pending", 0),
        "approved": counts.get("approved", 0),
        "sending": counts.get("sending", 0),
        "retry_pending": int(retry_pending),
        "stale_claims": int(stale_claims),
        "oldest_pending_at": _iso(oldest),
        "oldest_pending_age_minutes": _age_minutes(oldest, now),
        "open_total": counts.get("pending", 0) + counts.get("approved", 0) + counts.get("sending", 0),
    }


def _email_section(db: Session, *, since: datetime, now: datetime,
                   organization_id: int | None) -> dict:
    window_filter = [EmailSendRequest.created_at >= since]
    if organization_id is not None:
        window_filter.append(EmailSendRequest.organization_id == organization_id)
    by_status = _group_counts(db, EmailSendRequest, EmailSendRequest.status, *window_filter)
    success = sum(by_status.get(status, 0) for status in EMAIL_SUCCESS)
    dead = by_status.get("dead_letter", 0)

    dead_filter = [EmailSendRequest.status == "dead_letter"]
    if organization_id is not None:
        dead_filter.append(EmailSendRequest.organization_id == organization_id)
    dead_rows = (
        db.query(EmailSendRequest)
        .filter(*dead_filter)
        .order_by(EmailSendRequest.dead_letter_at.desc(), EmailSendRequest.id.desc())
        .limit(DEAD_LETTER_PAGE_SIZE)
        .all()
    )
    dead_codes = db.query(
        EmailSendRequest.error_code, func.count()
    ).filter(*window_filter, EmailSendRequest.status.in_(EMAIL_FAILURE)).group_by(
        EmailSendRequest.error_code,
    ).order_by(func.count().desc()).limit(FAILURE_CODE_LIMIT).all()
    sent_24h = db.query(func.count(EmailSendRequest.id)).filter(
        EmailSendRequest.status == "sent",
        EmailSendRequest.sent_at.isnot(None),
        EmailSendRequest.sent_at >= now - timedelta(hours=24),
    ).scalar() or 0

    policy_query = db.query(OutboundEmailPolicy)
    if organization_id is not None:
        policy_query = policy_query.filter(OutboundEmailPolicy.organization_id == organization_id)
    policy_enabled = any(policy.enabled for policy in policy_query.all())

    return {
        "total": sum(by_status.values()),
        "by_status": by_status,
        "rate": _rate(success, by_status.get("failed", 0), dead),
        "sent": success,
        "failed": by_status.get("failed", 0),
        "dead_letter": dead,
        "blocked": by_status.get("blocked", 0),
        "rejected": by_status.get("rejected", 0),
        "dlp_blocked": by_status.get("blocked", 0),
        "sent_24h": int(sent_24h),
        "policy_enabled": policy_enabled,
        "backlog": _email_backlog(db, now, organization_id),
        "dead_letters": {
            "total": dead,
            "items": [{
                "id": row.id,
                "recipient": _mask_recipient(row.recipient),
                "subject": row.subject,
                "attempt": row.attempt,
                "error_code": row.error_code,
                "error_message": row.sanitized_error_message or row.error_message,
                "dead_letter_reason": row.dead_letter_reason,
                "dead_letter_at": _iso(row.dead_letter_at),
                "notification_event_id": row.notification_event_id,
            } for row in dead_rows],
        },
        "failure_codes": [
            {"code": code or "unknown", "count": int(count)} for code, count in dead_codes
        ],
    }


def _dead_letter_count(db: Session, model, organization_id: int | None) -> int:
    return db.query(func.count(model.id)).filter(
        model.status == "dead_letter", *_org_filter(model, organization_id)
    ).scalar() or 0


def backlog_metrics(db: Session, *, organization_id: int | None = None,
                    now: datetime | None = None) -> dict:
    """积压/死信/重试/卡死的一次性读数；健康检查与快照采样共用。"""
    now = now or utc_now()
    return {
        "notification": _notification_backlog(db, now, organization_id),
        "email": _email_backlog(db, now, organization_id),
        "notification_dead_letter": _dead_letter_count(
            db, LegalNotificationEvent, organization_id),
        "email_dead_letter": _dead_letter_count(db, EmailSendRequest, organization_id),
    }


def evaluate_health(db: Session, *, organization_id: int | None = None,
                    now: datetime | None = None) -> dict:
    """投递健康阈值检查（看板与定时告警共用同一份口径）。

    阈值语义：``value >= threshold`` 视为异常。默认值见
    ``app/core/config/reliability.py`` 的 ``OUTBOX_*_WARN_THRESHOLD``。
    """
    from app.core.config import get_settings

    settings = get_settings()
    metrics = backlog_metrics(db, organization_id=organization_id, now=now)
    notification_backlog = metrics["notification"]
    email_backlog = metrics["email"]
    notification_dead = metrics["notification_dead_letter"]
    email_dead = metrics["email_dead_letter"]
    stale = notification_backlog["stale_claims"] + email_backlog["stale_claims"]
    retry = notification_backlog["retry_pending"] + email_backlog["retry_pending"]

    checks = [
        {
            "code": "NOTIFICATION_BACKLOG", "label": "通知待投递积压",
            "value": notification_backlog["open_total"],
            "threshold": settings.OUTBOX_BACKLOG_WARN_THRESHOLD, "level": "warning",
            "hint": "pending + approved + sending",
        },
        {
            "code": "EMAIL_BACKLOG", "label": "邮件待投递积压",
            "value": email_backlog["open_total"],
            "threshold": settings.OUTBOX_BACKLOG_WARN_THRESHOLD, "level": "warning",
            "hint": "待审批 + 待投递 + 投递中",
        },
        {
            "code": "DEAD_LETTER", "label": "死信",
            "value": notification_dead + email_dead,
            "threshold": settings.OUTBOX_DEAD_LETTER_WARN_THRESHOLD, "level": "warning",
            "hint": f"通知 {notification_dead} / 邮件 {email_dead}",
        },
        {
            "code": "STALE_CLAIMS", "label": "卡死租约",
            "value": stale,
            "threshold": settings.OUTBOX_STALE_CLAIM_WARN_THRESHOLD, "level": "warning",
            "hint": "worker 崩溃或卡死，等待回收任务重领",
        },
        {
            "code": "RETRY_STUCK", "label": "到期待重试",
            "value": retry,
            "threshold": settings.OUTBOX_RETRY_STUCK_WARN_THRESHOLD, "level": "info",
            "hint": "已过重试时间仍在等待 worker",
        },
    ]
    breached = [dict(check, breached=True) for check in checks
                if check["value"] >= check["threshold"]]
    return {
        "organization_id": organization_id,
        "checks": checks,
        "breached": breached,
        "healthy": not breached,
        "cooldown_minutes": settings.OUTBOX_ALERT_COOLDOWN_MINUTES,
    }


def _daily_status_counts(db: Session, model, since: datetime,
                         organization_id: int | None) -> dict[str, dict]:
    """按创建日聚合当前状态（不是当日投递量：没有快照，只能看队列批次结果）。"""
    rows = (
        db.query(func.date(model.created_at), model.status, func.count())
        .filter(model.created_at >= since, *_org_filter(model, organization_id))
        .group_by(func.date(model.created_at), model.status)
        .all()
    )
    success = NOTIFICATION_SUCCESS if model is LegalNotificationEvent else EMAIL_SUCCESS
    backlog = NOTIFICATION_BACKLOG if model is LegalNotificationEvent else EMAIL_BACKLOG
    buckets: dict[str, dict] = {}
    for day, status, count in rows:
        bucket = buckets.setdefault(str(day), {
            "created": 0, "delivered": 0, "failed": 0,
            "dead_letter": 0, "open": 0, "other": 0,
        })
        count = int(count)
        bucket["created"] += count
        if status in success:
            bucket["delivered"] += count
        elif status == "failed":
            bucket["failed"] += count
        elif status == "dead_letter":
            bucket["dead_letter"] += count
        elif status in backlog:
            bucket["open"] += count
        else:
            bucket["other"] += count
    return buckets


def daily_trend(db: Session, *, days: int = WINDOW_DEFAULT_DAYS,
                organization_id: int | None = None,
                now: datetime | None = None) -> dict:
    """按天趋势：每天创建的事件数 + 当前状态分布（补齐空白天，最多 30 桶）。"""
    days = int(days) if days is not None else WINDOW_DEFAULT_DAYS
    days = max(1, min(days, TREND_MAX_BUCKETS))
    now = now or utc_now()
    since = now - timedelta(days=days - 1)

    def _series(model) -> list[dict]:
        buckets = _daily_status_counts(db, model, since, organization_id)
        series = []
        cursor = since.date()
        while cursor <= now.date():
            key = cursor.isoformat()
            bucket = buckets.get(key) or {
                "created": 0, "delivered": 0, "failed": 0,
                "dead_letter": 0, "open": 0, "other": 0,
            }
            series.append({
                "date": key,
                **bucket,
                "rate": _rate(bucket["delivered"], bucket["failed"], bucket["dead_letter"]),
            })
            cursor += timedelta(days=1)
        return series

    return {
        "days": days,
        "notification": _series(LegalNotificationEvent),
        "email": _series(EmailSendRequest),
        "metric": "created_at 分桶 + 当前状态（不是当日投递量）",
    }


_ALERT_MESSAGES = {
    "NOTIFICATION_BACKLOG": "通知待投递积压 {value} 条（阈值 {threshold}），dispatch worker 可能停摆",
    "EMAIL_BACKLOG": "邮件待投递积压 {value} 条（阈值 {threshold}），邮件 worker 可能停摆或待审批堆积",
    "DEAD_LETTER": "{value} 条投递进入死信（阈值 {threshold}），需人工重试或修复模板",
    "STALE_CLAIMS": "{value} 条投递租约过期（阈值 {threshold}），worker 崩溃或卡死",
    "RETRY_STUCK": "{value} 条失败投递到期待重试（阈值 {threshold}），等待 worker 领取",
}


def _alerts(health: dict, email: dict) -> list[dict]:
    """人类可读提示：阈值超限 + 配置类提示。健康检查是结构化版本，这里不重复计算。"""
    alerts = []
    for check in health["breached"]:
        template = _ALERT_MESSAGES.get(check["code"])
        if not template:
            continue
        alerts.append({
            "code": check["code"],
            "level": check["level"],
            "message": template.format(value=check["value"], threshold=check["threshold"]),
        })
    if email["total"] and not email["policy_enabled"]:
        alerts.append({
            "code": "EMAIL_POLICY_DISABLED",
            "level": "info",
            "message": "有邮件投递记录但组织外发策略未启用，新通知不会走邮件渠道",
        })
    return alerts


def open_alert_states(db: Session, *, organization_id: int | None = None,
                      limit: int = 20) -> list[dict]:
    """仍在越阈值的告警状态（看板展示升级进度用）；已恢复的不返回。"""
    query = db.query(OutboxAlertState).filter(OutboxAlertState.first_breached_at.isnot(None))
    if organization_id is not None:
        query = query.filter(OutboxAlertState.organization_id == organization_id)
    rows = query.order_by(OutboxAlertState.first_breached_at.asc()).limit(limit).all()
    return [{
        "organization_id": row.organization_id,
        "breach_streak": row.breach_streak or 0,
        "first_breached_at": _iso(row.first_breached_at),
        "last_breached_at": _iso(row.last_breached_at),
        "last_alert_at": _iso(row.last_alert_at),
        "escalated_at": _iso(row.escalated_at),
        "resolved_at": _iso(row.resolved_at),
    } for row in rows]


def outbox_overview(db: Session, *, days: int = WINDOW_DEFAULT_DAYS,
                    organization_id: int | None = None,
                    now: datetime | None = None) -> dict:
    """投递看板数据：窗口内摘要在前，当前积压/死信/健康/趋势在后。"""
    days = int(days) if days is not None else WINDOW_DEFAULT_DAYS
    days = max(1, min(days, WINDOW_MAX_DAYS))
    now = now or utc_now()
    since = now - timedelta(days=days)

    notification = _notification_section(db, since=since, now=now)
    email = _email_section(db, since=since, now=now, organization_id=organization_id)
    health = evaluate_health(db, organization_id=organization_id, now=now)
    return {
        "window": {"days": days, "start": _iso(since), "end": _iso(now)},
        "generated_at": _iso(now),
        "notification": notification,
        "email": email,
        "trend": daily_trend(db, days=days, organization_id=organization_id, now=now),
        "health": health,
        "escalations": open_alert_states(db, organization_id=organization_id),
        "alerts": _alerts(health, email),
    }


# ── 人工重试：死信重排（权限由调用方校验）────────────────────────────────────

def can_operate_dead_letter(user: User, organization_id: int | None) -> bool:
    """平台管理员（无组织）可操作任意组织；组织管理员只能操作本组织。"""
    if user.role != "admin":
        return False
    if user.organization_id is None or organization_id is None:
        return True
    return user.organization_id == organization_id


def retry_dead_letter(db: Session, *, kind: str, item_id: int, user: User) -> dict:
    """把死信重排为待投递（notification）或待审批（email），返回新状态。

    重排只重置投递列与尝试次数，不改业务内容；幂等键保留，重复创建仍被去重。
    """
    from app.services.observability.oplog_service import oplog_service

    if kind == "notification":
        event = db.query(LegalNotificationEvent).filter(LegalNotificationEvent.id == item_id).first()
        if event is None:
            raise LookupError("NOTIFICATION_EVENT_NOT_FOUND")
        if not can_operate_dead_letter(user, event.organization_id):
            raise PermissionError("OUTBOX_RETRY_FORBIDDEN")
        from app.services.notification.notification_service import notification_service

        notification_service.requeue_dead_letter(db=db, event=event)
        db.commit()
        db.refresh(event)
        oplog_service.log(module="notification", action="outbox_dead_letter_retried", db=db,
                          user_id=user.id, target_type="notification_event", target_id=event.id,
                          detail=f"channel={event.channel}; template_key={event.template_key or ''}")
        return {"kind": kind, "id": event.id, "status": event.status}

    if kind == "email":
        request = db.query(EmailSendRequest).filter(EmailSendRequest.id == item_id).first()
        if request is None:
            raise LookupError("EMAIL_SEND_REQUEST_NOT_FOUND")
        if not can_operate_dead_letter(user, request.organization_id):
            raise PermissionError("OUTBOX_RETRY_FORBIDDEN")
        from app.services.notification.outbound_email_service import outbound_email_service

        outbound_email_service.requeue_dead_letter(request, db=db)
        db.commit()
        db.refresh(request)
        oplog_service.log(module="outbound_email", action="outbox_dead_letter_retried", db=db,
                          user_id=user.id, target_type="email_send_request", target_id=request.id,
                          detail=f"draft_id={request.draft_id}; idempotency_key={request.idempotency_key}")
        return {"kind": kind, "id": request.id, "status": request.status}

    raise ValueError("UNSUPPORTED_OUTBOX_KIND")
