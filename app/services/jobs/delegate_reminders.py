"""通知代理到期提醒：每天扫描 delegate_end 在 2 天内的偏好，提醒原收件人一次。

只提醒原收件人（allow_delegate=False）：这条消息本身就是「代理要结束了」，
不能再转给代理。按偏好时区的当地日期计算剩余天数，起止含当天。
去重：同一偏好、同一结束日期、同一标题只发一次；延长结束日期会得到新标题，重新提醒。
"""

from __future__ import annotations

from datetime import datetime, tzinfo, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models.legal_notifications import LegalNotificationEvent, LegalNotificationPreference
from app.models.user import User
from app.services.notification.notification_service import notification_service

REMIND_DAYS = 2
TEMPLATE_KEY = "delegate_expiring_soon"
DEFAULT_TIMEZONE = "Asia/Shanghai"


def _zone(pref: LegalNotificationPreference) -> tzinfo:
    try:
        return ZoneInfo(pref.timezone or DEFAULT_TIMEZONE)
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


def scan_delegate_expiring(db: Session, *, now: datetime | None = None) -> dict:
    """扫描代理有效期，返回 ``created`` / ``scanned``。"""
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
    for pref in prefs:
        if not pref.organization_id:
            continue
        today = moment.astimezone(_zone(pref)).date()
        days = (pref.delegate_end - today).days
        if days < 0 or days > REMIND_DAYS:
            continue
        title = f"通知代理将于 {pref.delegate_end:%Y-%m-%d} 到期"
        exists = (
            db.query(LegalNotificationEvent.id)
            .filter(
                LegalNotificationEvent.user_id == pref.user_id,
                LegalNotificationEvent.template_key == TEMPLATE_KEY,
                LegalNotificationEvent.title == title,
            )
            .first()
        )
        if exists:
            continue
        body = (
            f"delegate:expiring · 代理 {_display_name(db, pref.delegate_user_id)} "
            f"将于{_when_label(days)}到期（{pref.delegate_end:%Y-%m-%d}），"
            "之后通知恢复发送给你"
        )
        notification_service.create_multi_channel_notification(
            db=db,
            organization_id=pref.organization_id,
            user_id=pref.user_id,
            event_type="deadline",
            title=title,
            body=body,
            channels=["site", "email"],
            reference_type="notification_preference",
            reference_id=pref.id,
            business_version=pref.delegate_end.toordinal(),
            template_key=TEMPLATE_KEY,
            allow_delegate=False,
        )
        created += 1

    db.commit()
    return {"created": created, "scanned": len(prefs)}
