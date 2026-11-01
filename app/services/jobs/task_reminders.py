"""任务到期/逾期提醒：提前 1 天、当天、逾期 1 天、逾期 3 天各发一次。

由 Celery beat 每 15 分钟调用；也便于单测直接调用。
去重：同一任务同一阶段用 `LegalNotificationEvent(template_key=...)` 记一次，
重复扫描不会刷屏；四个阶段互相独立。

时间口径：`tasks.due_date` 是 naive 本地时间（step86 起前端写当天 23:59:59），
这里用**任务 owner 的通知偏好时区**（默认 Asia/Shanghai）判断「今天/明天/逾期」；
`now` 统一按 UTC 解释，再换算到用户时区，避免服务器时区影响提醒日期。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models.legal_notifications import LegalNotificationEvent
from app.models.task import Task
from app.models.user import User
from app.services.notification.notification_service import notification_service

ACTIVE_STATUSES = ("todo", "in_progress")
DEFAULT_TIMEZONE = "Asia/Shanghai"

# delta = due_day - user_today；四个阶段分别对应到期前 1 天/当天/逾期 1 天/逾期 3 天。
STAGES = {
    1: ("tomorrow", "task_due_tomorrow", "任务明天到期"),
    0: ("today", "task_due_today", "任务今天到期"),
    -1: ("overdue_1d", "task_overdue_1d", "任务已逾期 1 天"),
    -3: ("overdue_3d", "task_overdue_3d", "任务已逾期 3 天"),
}


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return utc_now()
    if value.tzinfo is None:
        # 历史调用方/单测传 naive：按 UTC 解释；用户时区只影响日期换算。
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _org_id(db: Session, task: Task) -> int | None:
    if task.organization_id:
        return task.organization_id
    user = db.get(User, task.user_id)
    return user.organization_id if user is not None else None


def _zone_for(
    db: Session, task: Task, org_id: int, cache: dict[tuple[int, int], ZoneInfo]
) -> ZoneInfo:
    key = (task.user_id, org_id)
    if key in cache:
        return cache[key]
    pref = notification_service.preference_for(
        db=db, user_id=task.user_id, organization_id=org_id, event_type="deadline"
    )
    name = getattr(pref, "timezone", None) or DEFAULT_TIMEZONE
    try:
        zone = ZoneInfo(name)
    except Exception:  # noqa: BLE001 - 时区非法按默认时区，不阻塞提醒
        zone = ZoneInfo(DEFAULT_TIMEZONE)
    cache[key] = zone
    return zone


def _title(task: Task) -> str:
    text = str(task.title or "").strip().splitlines()[0] if str(task.title or "").strip() else f"任务 #{task.id}"
    return text[:40]


def scan_task_due_reminders(db: Session, *, now: datetime | None = None) -> dict:
    """扫描任务并发到期/逾期提醒，返回各阶段计数。"""
    moment = _utc(now)

    rows = (
        db.query(Task)
        .filter(
            Task.due_date.isnot(None),
            Task.status.in_(ACTIVE_STATUSES),
            Task.archived_at.is_(None),
        )
        .all()
    )
    created = skipped_no_org = 0
    counts = {"tomorrow": 0, "today": 0, "overdue_1d": 0, "overdue_3d": 0}
    zone_cache: dict[tuple[int, int], ZoneInfo] = {}
    today_cache: dict[tuple[int, int], object] = {}

    for task in rows:
        org_id = _org_id(db, task)
        if org_id:
            zone = _zone_for(db, task, org_id, zone_cache)
            user_key = (task.user_id, org_id)
        else:
            # 无组织任务无法定位偏好，退化到默认时区判断是否命中阶段；命中后跳过。
            zone = ZoneInfo(DEFAULT_TIMEZONE)
            user_key = (task.user_id, 0)
        local_today = today_cache.get(user_key)
        if local_today is None:
            local_today = moment.astimezone(zone).date()
            today_cache[user_key] = local_today

        due = task.due_date
        if due.tzinfo is not None:
            due_day = due.astimezone(zone).date()
        else:
            due_day = due.date()
        delta = (due_day - local_today).days
        stage_info = STAGES.get(delta)
        if stage_info is None:
            continue
        if not org_id:
            skipped_no_org += 1
            continue

        stage, template_key, prefix = stage_info
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
        overdue_suffix = f"（已逾期 {abs(delta)} 天）" if delta < 0 else ""
        body = f"task:{stage} · 任务 #{task.id} 的截止日期为 {due:%Y-%m-%d}{overdue_suffix}"
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
        "overdue_1d": counts["overdue_1d"],
        "overdue_3d": counts["overdue_3d"],
        "skipped_no_org": skipped_no_org,
    }
