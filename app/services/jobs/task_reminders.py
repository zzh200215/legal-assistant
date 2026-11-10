"""任务到期/逾期提醒：负责人 + 协作者，提前 1 天、当天、逾期 1 天、逾期 3 天各发一次。

由 Celery beat 每 15 分钟调用；也便于单测直接调用。
收件人：负责人优先，没有负责人时回退到 owner；协作者一并提醒。
同一任务、同一收件人、同一阶段只发一次（``LegalNotificationEvent`` 记一次），
重复扫描不会刷屏；四个阶段互相独立；同一人既是负责人又是协作者时只发一次。
协作者使用 ``task_collaborator_*`` 模板，站内标题带「协作者提醒」，邮件文案强调
「你参与的任务」，便于和负责人提醒区分。

时间口径：`tasks.due_date` 是 naive 本地时间（step86 起前端写当天 23:59:59），
这里用**每个收件人自己的通知偏好时区**（默认 Asia/Shanghai）判断「今天/明天/逾期」；
`now` 统一按 UTC 解释，再换算到用户时区，避免服务器时区影响提醒日期。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
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

# 协作者模板：阶段语义与负责人一致，只换文案与模板键，去重互不影响。
COLLABORATOR_TEMPLATES = {
    "task_due_tomorrow": "task_collaborator_due_tomorrow",
    "task_due_today": "task_collaborator_due_today",
    "task_overdue_1d": "task_collaborator_overdue_1d",
    "task_overdue_3d": "task_collaborator_overdue_3d",
}

# owner 升级：任务逾期 3 天及以上、负责人/协作者仍未完成，且 owner 不在普通收件人里。
ESCALATION_TEMPLATE = "task_owner_escalation_overdue_3d"
ESCALATION_AFTER_DAYS = 3


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
    db: Session, user_id: int, org_id: int, cache: dict[tuple[int, int], ZoneInfo]
) -> ZoneInfo:
    key = (user_id, org_id)
    if key in cache:
        return cache[key]
    pref = notification_service.preference_for(
        db=db, user_id=user_id, organization_id=org_id, event_type="deadline"
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


def _display_name(db: Session, user_id: int | None) -> str:
    user = db.get(User, user_id) if user_id else None
    if user is None:
        return "未指派" if not user_id else f"用户 {user_id}"
    return (user.full_name or user.username or f"用户 {user.id}").strip()[:128]


def _delta_days(task: Task, zone: ZoneInfo, local_today: date) -> int:
    """截止日期相对「收件人所在地的今天」还有几天（负数=逾期）。"""
    due = task.due_date
    due_day = due.astimezone(zone).date() if due.tzinfo is not None else due.date()
    return (due_day - local_today).days


def _recipients(task: Task) -> list[tuple[int, bool]]:
    """返回 ``[(user_id, is_collaborator)]``：负责人优先，无负责人回退 owner，最后协作者。

    同一人只出现一次：负责人/owner 已在列表里时，对应的协作者记录跳过。
    """
    primary = task.assignee_user_id or task.user_id
    recipients: list[tuple[int, bool]] = [(primary, False)]
    seen = {primary}
    for link in sorted(task.collaborator_links, key=lambda item: item.id or 0):
        if link.user_id in seen:
            continue
        seen.add(link.user_id)
        recipients.append((link.user_id, True))
    return recipients


def _existing_reminders(db: Session, task_ids: list[int]) -> set[tuple[int, int, str]]:
    """预取已发提醒，避免逐个收件人查询；键为 (task_id, user_id, template_key)。"""
    if not task_ids:
        return set()
    rows = (
        db.query(
            LegalNotificationEvent.reference_id,
            LegalNotificationEvent.user_id,
            LegalNotificationEvent.template_key,
        )
        .filter(
            LegalNotificationEvent.reference_type == "task",
            LegalNotificationEvent.reference_id.in_(task_ids),
            LegalNotificationEvent.template_key.isnot(None),
        )
        .all()
    )
    return {(int(rid), int(uid), str(key)) for rid, uid, key in rows}


def scan_task_due_reminders(db: Session, *, now: datetime | None = None) -> dict:
    """扫描任务并发到期/逾期提醒与 owner 升级，返回各阶段提醒条数（按收件人计）。"""
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
    created = collaborator_created = escalated = skipped_no_org = 0
    counts = {"tomorrow": 0, "today": 0, "overdue_1d": 0, "overdue_3d": 0}
    zone_cache: dict[tuple[int, int], ZoneInfo] = {}
    today_cache: dict[tuple[int, int], date] = {}
    existing = _existing_reminders(db, [task.id for task in rows])

    for task in rows:
        org_id = _org_id(db, task)
        recipients = _recipients(task)
        hit_without_org = False
        for recipient_id, is_collaborator in recipients:
            if org_id:
                zone = _zone_for(db, recipient_id, org_id, zone_cache)
                user_key = (recipient_id, org_id)
            else:
                # 无组织任务无法定位偏好，退化到默认时区判断是否命中阶段；命中后跳过。
                zone = ZoneInfo(DEFAULT_TIMEZONE)
                user_key = (recipient_id, 0)
            local_today = today_cache.get(user_key)
            if local_today is None:
                local_today = moment.astimezone(zone).date()
                today_cache[user_key] = local_today

            delta = _delta_days(task, zone, local_today)
            stage_info = STAGES.get(delta)
            if stage_info is None:
                continue
            if not org_id:
                hit_without_org = True
                continue

            stage, base_template_key, prefix = stage_info
            template_key = (
                COLLABORATOR_TEMPLATES[base_template_key] if is_collaborator else base_template_key
            )
            if (task.id, recipient_id, template_key) in existing:
                continue

            title = _title(task)
            due = task.due_date
            overdue_suffix = f"（已逾期 {abs(delta)} 天）" if delta < 0 else ""
            body = f"task:{stage} · 任务 #{task.id} 的截止日期为 {due:%Y-%m-%d}{overdue_suffix}"
            reminder_prefix = f"协作者提醒 · {prefix}" if is_collaborator else prefix
            # 站内即时创建；邮件仅在组织配置外发策略时由 Outbox 真正投递。
            notification_service.create_multi_channel_notification(
                db=db,
                organization_id=org_id,
                user_id=recipient_id,
                event_type="deadline",
                title=f"{reminder_prefix}：{title}",
                body=body,
                channels=["site", "email"],
                case_id=task.case_id,
                reference_type="task",
                reference_id=task.id,
                template_key=template_key,
            )
            existing.add((task.id, recipient_id, template_key))
            created += 1
            if is_collaborator:
                collaborator_created += 1
            counts[stage] += 1

        # owner 升级：只在 task 有负责人、且 owner 本人不在普通收件人里时发一次。
        owner_id = task.user_id
        if org_id and task.assignee_user_id and owner_id not in {uid for uid, _ in recipients}:
            owner_zone = _zone_for(db, owner_id, org_id, zone_cache)
            owner_key = (owner_id, org_id)
            owner_today = today_cache.get(owner_key)
            if owner_today is None:
                owner_today = moment.astimezone(owner_zone).date()
                today_cache[owner_key] = owner_today
            owner_delta = _delta_days(task, owner_zone, owner_today)
            already_escalated = (task.id, owner_id, ESCALATION_TEMPLATE) in existing
            if owner_delta <= -ESCALATION_AFTER_DAYS and not already_escalated:
                overdue_days = abs(owner_delta)
                collaborator_count = max(0, len(recipients) - 1)
                assignee_name = _display_name(db, task.assignee_user_id)
                pending_text = (
                    f"负责人 {assignee_name}、协作者 {collaborator_count} 人仍未完成"
                    if collaborator_count
                    else f"负责人 {assignee_name} 仍未完成"
                )
                notification_service.create_multi_channel_notification(
                    db=db,
                    organization_id=org_id,
                    user_id=owner_id,
                    event_type="deadline",
                    title=f"任务逾期 {overdue_days} 天升级：{_title(task)}",
                    body=(
                        f"task:owner_escalation · 任务 #{task.id} 已逾期 {overdue_days} 天，"
                        f"{pending_text}"
                    ),
                    channels=["site", "email"],
                    case_id=task.case_id,
                    reference_type="task",
                    reference_id=task.id,
                    template_key=ESCALATION_TEMPLATE,
                    respect_quiet_hours=False,
                )
                existing.add((task.id, owner_id, ESCALATION_TEMPLATE))
                created += 1
                escalated += 1

        if hit_without_org:
            skipped_no_org += 1

    db.commit()
    return {
        "created": created,
        "due_tomorrow": counts["tomorrow"],
        "due_today": counts["today"],
        "overdue_1d": counts["overdue_1d"],
        "overdue_3d": counts["overdue_3d"],
        "collaborator_created": collaborator_created,
        "escalated": escalated,
        "skipped_no_org": skipped_no_org,
    }
