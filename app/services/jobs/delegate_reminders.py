"""通知代理与请假：每天扫描代理/请假有效期，发提醒、代收开始/交还通知并清理过期配置。

覆盖两类配置：
- 按事件类型的代理（``legal_notification_preferences.delegate_end``）；
- 全局请假代理（``legal_notification_leaves``）。

到期提醒只提醒原收件人（``allow_delegate=False``）：这条消息本身就是「代理要结束了」，
不能再转给代理。按用户时区的当地日期计算剩余天数，起止含当天。
去重：同一配置、同一结束日期、同一标题只发一次；延长结束日期会得到新标题，重新提醒。
请假另有两条发给代理本人的通知：生效当天的「开始代收」，结束/换代理时的「已交还」，
都按请假轮次去重（见 notification_service.notify_leave_started / notify_leave_ended）。
到期后清空代理与有效期（请假还把备注一起清掉），通知自动恢复发给本人。
"""

from __future__ import annotations

from datetime import datetime, tzinfo, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models.legal_notifications import (
    LegalNotificationEvent,
    LegalNotificationLeave,
    LegalNotificationPreference,
)
from app.models.user import User
from app.services.notification.notification_service import notification_service

REMIND_DAYS = 2
TEMPLATE_KEY = "delegate_expiring_soon"
LEAVE_TEMPLATE_KEY = "leave_expiring_soon"
DEFAULT_TIMEZONE = "Asia/Shanghai"


def _zone(name: str | None) -> tzinfo:
    try:
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except Exception:  # noqa: BLE001 - 时区非法按 UTC 解释，不阻塞提醒
        return timezone.utc


def _display_name(db: Session, user_id: int) -> str:
    user = db.get(User, user_id)
    if user is None:
        return f"用户 {user_id}"
    return (user.full_name or user.username or f"用户 {user.id}").strip()[:128]


def _when_label(days: int) -> str:
    if days == 0:
        return "今天"
    if days == 1:
        return "明天"
    return f"{days} 天后"


def _already_reminded(db: Session, *, user_id: int, template_key: str, title: str) -> bool:
    return (
        db.query(LegalNotificationEvent.id)
        .filter(
            LegalNotificationEvent.user_id == user_id,
            LegalNotificationEvent.template_key == template_key,
            LegalNotificationEvent.title == title,
        )
        .first()
        is not None
    )


def _remind(db: Session, *, organization_id: int, user_id: int, title: str, body: str,
            template_key: str, reference_type: str, reference_id: int,
            business_version: int) -> None:
    notification_service.create_multi_channel_notification(
        db=db,
        organization_id=organization_id,
        user_id=user_id,
        event_type="deadline",
        title=title,
        body=body,
        channels=["site", "email"],
        reference_type=reference_type,
        reference_id=reference_id,
        business_version=business_version,
        template_key=template_key,
        allow_delegate=False,
    )


def scan_delegate_expiring(db: Session, *, now: datetime | None = None) -> dict:
    """扫描代理/请假有效期，返回创建与清理计数。

    ``created`` / ``scanned`` / ``cleared`` 是按事件类型的代理；
    ``leave_created`` / ``leave_scanned`` / ``leave_cleared`` 是全局请假的到期提醒与清理；
    ``leave_started`` / ``leave_ended`` 是发给代理本人的代收开始 / 交还通知。
    """
    moment = now or utc_now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)

    prefs = (
        db.query(LegalNotificationPreference)
        .filter(
            LegalNotificationPreference.delegate_user_id.isnot(None),
            LegalNotificationPreference.delegate_end.isnot(None),
        )
        .all()
    )
    created = 0
    cleared = 0
    for pref in prefs:
        if not pref.organization_id:
            continue
        today = moment.astimezone(_zone(pref.timezone)).date()
        days = (pref.delegate_end - today).days
        if days < 0:
            # 已过期：清空代理与有效期，避免设置残留；日期字段本身也一起清掉，
            # 否则页面会显示「没有代理，却有一段有效期」。
            pref.delegate_user_id = None
            pref.delegate_start = None
            pref.delegate_end = None
            cleared += 1
            continue
        if days > REMIND_DAYS:
            continue
        title = f"通知代理将于 {pref.delegate_end:%Y-%m-%d} 到期"
        if _already_reminded(db, user_id=pref.user_id, template_key=TEMPLATE_KEY, title=title):
            continue
        body = (
            f"delegate:expiring · 代理 {_display_name(db, pref.delegate_user_id)} "
            f"将于{_when_label(days)}到期（{pref.delegate_end:%Y-%m-%d}），"
            "之后通知恢复发送给你"
        )
        _remind(
            db, organization_id=pref.organization_id, user_id=pref.user_id,
            title=title, body=body, template_key=TEMPLATE_KEY,
            reference_type="notification_preference", reference_id=pref.id,
            business_version=pref.delegate_end.toordinal(),
        )
        created += 1

    leave_created, leave_cleared, leave_scanned, leave_started, leave_ended = _scan_leave(
        db, moment=moment)

    db.commit()
    return {
        "created": created,
        "scanned": len(prefs),
        "cleared": cleared,
        "leave_created": leave_created,
        "leave_scanned": leave_scanned,
        "leave_cleared": leave_cleared,
        "leave_started": leave_started,
        "leave_ended": leave_ended,
    }


def _scan_leave(db: Session, *, moment: datetime) -> tuple[int, int, int, int, int]:
    """扫描全局请假：生效通知、到期提醒、到期交还与清理。

    返回 ``(created, cleared, scanned, started, ended)``：``created``/``scanned``
    是到期提醒口径（只统计带结束日期的请假），``started``/``ended`` 是发给代理的
    开始代收 / 交还通知数量。
    """
    started = 0
    active_leaves = (
        db.query(LegalNotificationLeave)
        .filter(LegalNotificationLeave.delegate_user_id.isnot(None))
        .all()
    )
    for leave in active_leaves:
        if not leave.organization_id:
            continue
        if notification_service.notify_leave_started(db=db, leave=leave, now=moment):
            started += 1

    leaves = (
        db.query(LegalNotificationLeave)
        .filter(
            LegalNotificationLeave.delegate_user_id.isnot(None),
            LegalNotificationLeave.leave_end.isnot(None),
        )
        .all()
    )
    created = 0
    cleared = 0
    ended = 0
    for leave in leaves:
        if not leave.organization_id:
            continue
        timezone_name = notification_service.user_timezone(
            db=db, user_id=leave.user_id, organization_id=leave.organization_id,
        )
        today = moment.astimezone(_zone(timezone_name)).date()
        days = (leave.leave_end - today).days
        if days < 0:
            if notification_service.notify_leave_ended(db=db, leave=leave):
                ended += 1
            leave.delegate_user_id = None
            leave.leave_start = None
            leave.leave_end = None
            leave.note = None
            cleared += 1
            continue
        if days > REMIND_DAYS:
            continue
        title = f"请假代理将于 {leave.leave_end:%Y-%m-%d} 到期"
        if _already_reminded(db, user_id=leave.user_id,
                             template_key=LEAVE_TEMPLATE_KEY, title=title):
            continue
        body = (
            f"leave:expiring · 请假代理 {_display_name(db, leave.delegate_user_id)} "
            f"将于{_when_label(days)}到期（{leave.leave_end:%Y-%m-%d}），"
            "之后通知恢复发送给你"
        )
        _remind(
            db, organization_id=leave.organization_id, user_id=leave.user_id,
            title=title, body=body, template_key=LEAVE_TEMPLATE_KEY,
            reference_type="notification_leave", reference_id=leave.id,
            business_version=leave.leave_end.toordinal(),
        )
        created += 1
    return created, cleared, len(leaves), started, ended
