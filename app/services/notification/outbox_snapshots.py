"""投递积压快照：定时采样、保留期清理、历史曲线查询。

为什么需要快照：积压是「此刻有多少没投完」，属状态量，无法从事件明细反推历史值。
step41 的趋势是「创建日 + 当前状态」，回答不了「昨晚 2 点积压是不是涨到 300」。
本模块每 5 分钟写一行快照（平台级 + 每个有管理员的组织），看板据此画真实积压曲线。

吞吐（每小时创建/投递数）不需要快照，直接用 ``created_at`` / ``sent_at`` 分桶；
小时桶表达式按数据库方言取（SQLite ``strftime`` / MySQL ``date_format``），其它方言
回退到 SQLite 写法并在文档标注。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.time import utc_now
from app.models.email import EmailSendRequest
from app.models.legal_notifications import LegalNotificationEvent, OutboxSnapshot
from app.services.notification.delivery_ops import NOTIFICATION_SUCCESS, EMAIL_SUCCESS, backlog_metrics

HISTORY_DEFAULT_HOURS = 24
HISTORY_MAX_HOURS = 168


def _hour_expr(db: Session, column):
    """小时桶表达式：SQLite/MySQL 各用原生函数；其它方言按 SQLite 写法尝试。"""
    dialect = getattr(getattr(db, "bind", None), "dialect", None)
    name = getattr(dialect, "name", "")
    if name == "mysql":
        return func.date_format(column, "%Y-%m-%d %H")
    return func.strftime("%Y-%m-%d %H", column)


def _hourly_counts(db: Session, model, column, *, since: datetime,
                   extra_filters: list | None = None) -> dict[str, int]:
    expr = _hour_expr(db, column)
    query = db.query(expr, func.count()).filter(column >= since)
    for condition in extra_filters or []:
        query = query.filter(condition)
    rows = query.group_by(expr).all()
    return {str(bucket): int(count) for bucket, count in rows}


def _org_scope(model, organization_id: int | None) -> list:
    return [] if organization_id is None else [model.organization_id == organization_id]


def capture_snapshots(db: Session, *, now: datetime | None = None) -> dict:
    """采样一次积压快照（平台级 + 组织级），并顺带清理过期快照。"""
    from app.services.notification.outbox_alerts import admin_org_ids

    settings = get_settings()
    now = now or utc_now()
    limit = settings.OUTBOX_SNAPSHOT_MAX_ORGS
    org_ids = admin_org_ids(db)
    targets: list[int | None] = [None] + org_ids[:limit]

    captured = 0
    for organization_id in targets:
        metrics = backlog_metrics(db, organization_id=organization_id, now=now)
        notification = metrics["notification"]
        email = metrics["email"]
        db.add(OutboxSnapshot(
            captured_at=now,
            organization_id=organization_id,
            notification_backlog=notification["open_total"],
            notification_dead_letter=metrics["notification_dead_letter"],
            notification_retry_pending=notification["retry_pending"],
            notification_stale_claims=notification["stale_claims"],
            email_backlog=email["open_total"],
            email_dead_letter=metrics["email_dead_letter"],
            email_retry_pending=email["retry_pending"],
            email_stale_claims=email["stale_claims"],
        ))
        captured += 1
    db.commit()

    return {
        "captured": captured,
        "organizations": len(org_ids),
        "truncated_orgs": max(0, len(org_ids) - limit),
        "pruned": prune_snapshots(db, now=now),
    }


def prune_snapshots(db: Session, *, now: datetime | None = None) -> int:
    """删除超过保留期的快照；返回删除行数。"""
    settings = get_settings()
    now = now or utc_now()
    cutoff = now - timedelta(days=settings.OUTBOX_SNAPSHOT_RETENTION_DAYS)
    deleted = (
        db.query(OutboxSnapshot)
        .filter(OutboxSnapshot.captured_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    return int(deleted or 0)


def _hour_keys(*, hours: int, now: datetime) -> list[str]:
    """从当前小时往前补齐 hours 个键（含当前小时，空小时也返回）。"""
    end = now.replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=max(1, hours) - 1)
    keys: list[str] = []
    cursor = start
    while cursor <= end:
        keys.append(cursor.strftime("%Y-%m-%d %H"))
        cursor += timedelta(hours=1)
    return keys


def history(db: Session, *, hours: int = HISTORY_DEFAULT_HOURS,
            organization_id: int | None = None,
            now: datetime | None = None) -> dict:
    """积压曲线 + 每小时吞吐（创建/投递），供投递看板画图。"""
    hours = int(hours) if hours is not None else HISTORY_DEFAULT_HOURS
    hours = max(1, min(hours, HISTORY_MAX_HOURS))
    now = now or utc_now()
    since = now - timedelta(hours=hours)

    snapshot_query = db.query(OutboxSnapshot).filter(OutboxSnapshot.captured_at >= since)
    if organization_id is None:
        snapshot_query = snapshot_query.filter(OutboxSnapshot.organization_id.is_(None))
    else:
        snapshot_query = snapshot_query.filter(OutboxSnapshot.organization_id == organization_id)
    rows = snapshot_query.order_by(OutboxSnapshot.captured_at.asc()).limit(5000).all()

    points = [{
        "at": row.captured_at.isoformat() if row.captured_at else None,
        "notification": row.notification_backlog,
        "email": row.email_backlog,
        "notification_dead_letter": row.notification_dead_letter,
        "email_dead_letter": row.email_dead_letter,
        "notification_retry_pending": row.notification_retry_pending,
        "email_retry_pending": row.email_retry_pending,
    } for row in rows]

    notification_created = _hourly_counts(
        db, LegalNotificationEvent, LegalNotificationEvent.created_at, since=since,
        extra_filters=_org_scope(LegalNotificationEvent, organization_id))
    notification_delivered = _hourly_counts(
        db, LegalNotificationEvent, LegalNotificationEvent.sent_at, since=since,
        extra_filters=[LegalNotificationEvent.status.in_(NOTIFICATION_SUCCESS),
                       *_org_scope(LegalNotificationEvent, organization_id)])
    email_created = _hourly_counts(
        db, EmailSendRequest, EmailSendRequest.created_at, since=since,
        extra_filters=_org_scope(EmailSendRequest, organization_id))
    email_delivered = _hourly_counts(
        db, EmailSendRequest, EmailSendRequest.sent_at, since=since,
        extra_filters=[EmailSendRequest.status.in_(EMAIL_SUCCESS),
                       *_org_scope(EmailSendRequest, organization_id)])

    throughput = [{
        "hour": key,
        "notification_created": notification_created.get(key, 0),
        "notification_delivered": notification_delivered.get(key, 0),
        "email_created": email_created.get(key, 0),
        "email_delivered": email_delivered.get(key, 0),
    } for key in _hour_keys(hours=hours, now=now)]

    return {
        "hours": hours,
        "organization_id": organization_id,
        "snapshot_count": len(points),
        "empty": not points,
        "interval_note": "快照由 check_outbox_snapshot 每 5 分钟写入；无快照时积压曲线为空",
        "backlog": points,
        "throughput": throughput,
    }
