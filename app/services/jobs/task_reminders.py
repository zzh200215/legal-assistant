"""任务到期提醒：到期前 1 天 / 到期当天各发一次站内（+可选邮件）提醒。

由 Celery beat 每 15 分钟调用；也便于单测直接调用。
去重：同一任务同一阶段用 `LegalNotificationEvent(template_key=...)` 记一次，
重复扫描不会刷屏；「提前 1 天」和「当天」是两个独立阶段，会各提醒一次。

时间口径：`tasks.due_date` 是 naive 本地时间（step86 起由前端写当天 23:59:59），
这里也按服务器本地日期判断「今天/明天」，不做 UTC 换算。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.legal_notifications import LegalNotificationEvent
from app.models.task import Task
from app.models.user import User
from app.services.notification.notification_service import notification_service

ACTIVE_STATUSES = ("todo", "in_progress")
STAGES = {
    "tomorrow": ("task_due_tomorrow", "任务明天到期"),
    "today": ("task_due_today", "任务今天到期"),
}


def _local_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone().replace(tzinfo=None)


def _org_id(db: Session, task: Task) -> int | None:
    if task.organization_id:
        return task.organization_id
    user = db.get(User, task.user_id)
    return user.organization_id if user is not None else None


def _title(task: Task) -> str:
    text = str(task.title or "").strip().splitlines()[0] if str(task.title or "").strip() else f"任务 #{task.id}"
    return text[:40]


def scan_task_due_reminders(db: Session, *, now: datetime | None = None) -> dict:
    """扫描任务并发到期提醒，返回 {created, due_tomorrow, due_today, skipped_no_org}。"""
    current = _local_naive(now) or datetime.now()
    today = current.date()
    tomorrow = today + timedelta(days=1)

    rows = (
        db.query(Task)
        .filter(Task.due_date.isnot(None), Task.status.in_(ACTIVE_STATUSES))
        .all()
    )
    created = skipped_no_org = 0
    counts = {"tomorrow": 0, "today": 0}

    for task in rows:
        due = _local_naive(task.due_date)
        if due is None:
            continue
        due_day = due.date()
        if due_day == tomorrow:
            stage = "tomorrow"
        elif due_day == today:
            stage = "today"
        else:
            continue

        org_id = _org_id(db, task)
        if not org_id:
            skipped_no_org += 1
            continue

        template_key, prefix = STAGES[stage]
        exists = (
            db.query(LegalNotificationEvent.id)
            .filter(
                LegalNotificationEvent.reference_type == "task",
                LegalNotificationEvent.reference_id == task.id,
                LegalNotificationEvent.template_key == template_key,
            )
            .first()
        )
        if exists:
            continue

        title = _title(task)
        body = f"task:{stage} · 任务 #{task.id} 的截止日期为 {due:%Y-%m-%d}"
        # 站内即时创建；邮件仅在组织配置外发策略时由 Outbox 真正投递。
        notification_service.create_multi_channel_notification(
            db=db,
            organization_id=org_id,
            user_id=task.user_id,
            event_type="deadline",
            title=f"{prefix}：{title}",
            body=body,
            channels=["site", "email"],
            case_id=task.case_id,
            reference_type="task",
            reference_id=task.id,
            template_key=template_key,
        )
        created += 1
        counts[stage] += 1

    db.commit()
    return {
        "created": created,
        "due_tomorrow": counts["tomorrow"],
        "due_today": counts["today"],
        "skipped_no_org": skipped_no_org,
    }
