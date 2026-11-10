"""Outbox 投递告警：按组织评估阈值，超限时给组织管理员发站内 + 邮件通知。

为什么按组织而不是全局：
- 通知事件与邮件请求都有 ``organization_id``，租户隔离下「A 组织积压」不该吵到 B 组织；
- 平台管理员（``organization_id IS NULL``）不接收组织级事件（通知表要求 org 非空），
  这类账号只能在投递看板看全局视图——文档里明确写了这条边界。

三层节奏：
1. **普通告警**：越阈值即发，``business_version`` 取 ``OUTBOX_ALERT_COOLDOWN_MINUTES``
   窗口，同窗口内幂等（默认 180 分钟）；
2. **静默时段**：命中用户免打扰窗口时只发站内，并把「静默」写进正文；升级提醒不受限；
3. **升级提醒**：越阈值持续超过 ``OUTBOX_ALERT_ESCALATE_AFTER_MINUTES`` 后单独发一条
   （独立模板键 + 独立冷却窗口），并在 ``outbox_alert_states`` 记录持续次数与时间。

恢复判定：某次扫描不再越阈值时，清零 streak、记 ``resolved_at``，之后重新计时。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.time import utc_now
from app.models.legal_notifications import OutboxAlertState
from app.models.user import User
from app.services.notification.delivery_ops import evaluate_health
from app.services.notification.notification_service import notification_service

EVENT_TYPE = "approval"
TEMPLATE_KEY = "outbox_health_alert"
ESCALATION_TEMPLATE_KEY = "outbox_health_escalation"
REFERENCE_TYPE = "outbox_health"
TITLE_TEMPLATE = "投递告警：{count} 项超阈值"
ESCALATION_TITLE_TEMPLATE = "投递告警升级：已持续 {minutes} 分钟"
QUIET_SUFFIX = "（静默时段，仅站内提醒；升级提醒不受静默限制）"

logger = logging.getLogger(__name__)


def admin_org_ids(db: Session) -> list[int]:
    """有管理员账号的组织（告警只发给这些组织，平台账号 organization_id 为空）。"""
    rows = (
        db.query(User.organization_id)
        .filter(User.role == "admin", User.organization_id.isnot(None))
        .distinct()
        .all()
    )
    return sorted({org_id for (org_id,) in rows if org_id is not None})


def _org_admin_ids(db: Session, organization_id: int) -> list[int]:
    rows = (
        db.query(User.id)
        .filter(User.role == "admin", User.organization_id == organization_id)
        .all()
    )
    return [user_id for (user_id,) in rows]


def _state_for(db: Session, organization_id: int) -> OutboxAlertState:
    state = (
        db.query(OutboxAlertState)
        .filter(OutboxAlertState.organization_id == organization_id)
        .first()
    )
    if state is None:
        state = OutboxAlertState(organization_id=organization_id, breach_streak=0)
        db.add(state)
        db.flush()
    return state


def _alert_body(breached: list[dict], cooldown_minutes: int) -> str:
    parts = [
        f"{check['label']} {check['value']} 条（阈值 {check['threshold']}）"
        for check in breached
    ]
    return f"{'；'.join(parts)}。请到「系统 → 投递看板」处理；同类告警 {cooldown_minutes} 分钟内不重复发送。"


def _channels_for(db: Session, *, user_id: int, organization_id: int,
                  now: datetime, escalate: bool) -> tuple[list[str], bool]:
    """解析本次告警渠道：静默时段普通告警只发站内；升级提醒始终双渠道。"""
    settings = get_settings()
    if escalate or not settings.OUTBOX_ALERT_RESPECT_QUIET_HOURS:
        return ["site", "email"], False
    pref = notification_service.preference_for(
        db=db, user_id=user_id, organization_id=organization_id, event_type=EVENT_TYPE,
    )
    if notification_service.is_quiet_now(pref, now=now):
        return ["site"], True
    return ["site", "email"], False


def _notify(db: Session, *, organization_id: int, admin_ids: list[int], title: str,
            body: str, template_key: str, business_version: int, now: datetime,
            escalate: bool) -> tuple[int, int]:
    """给组织管理员发一轮告警，返回 (事件数, 被静默的用户数)。"""
    events = 0
    muted = 0
    for user_id in admin_ids:
        channels, quiet = _channels_for(
            db, user_id=user_id, organization_id=organization_id, now=now, escalate=escalate,
        )
        muted += 1 if quiet else 0
        created = notification_service.create_multi_channel_notification(
            db=db,
            organization_id=organization_id,
            user_id=user_id,
            event_type=EVENT_TYPE,
            title=title,
            body=body + (QUIET_SUFFIX if quiet else ""),
            channels=channels,
            reference_type=REFERENCE_TYPE,
            reference_id=organization_id,
            # 冷却窗口进幂等键：窗口内重复扫描不会产生新事件
            business_version=business_version,
            template_key=template_key,
            # 告警自己按 _channels_for 处理静默（普通告警降为站内、升级穿透），
            # 不再走通用的「延迟到窗口结束」逻辑。
            respect_quiet_hours=False,
            # 投递告警是平台运维提醒，不转给个人代理。
            allow_delegate=False,
        )
        events += len(created)
    return events, muted


def run_outbox_health_check(db: Session, *, now: datetime | None = None) -> dict:
    """扫描有管理员的组织：发普通告警、按持续时长升级、恢复时清零状态。"""
    settings = get_settings()
    now = now or utc_now()
    cooldown = settings.OUTBOX_ALERT_COOLDOWN_MINUTES
    window = int(now.timestamp() // (cooldown * 60))
    escalation_cooldown = settings.OUTBOX_ESCALATION_COOLDOWN_MINUTES
    escalation_window = int(now.timestamp() // (escalation_cooldown * 60))

    stats = {
        "checked_orgs": 0,
        "breached_orgs": 0,
        "notified_users": 0,
        "events": 0,
        "quiet_users": 0,
        "escalated_orgs": 0,
        "escalation_events": 0,
        "resolved_orgs": 0,
        "breached": [],
        "skipped": None,
    }
    org_ids = admin_org_ids(db)
    if not org_ids:
        # 平台管理员（无组织）不接收组织级通知事件；这不是失败，但必须让日志/接口看得见
        logger.warning("outbox health check: 没有带组织的管理员账号，告警无处可发")
        stats["skipped"] = "no_org_admin"
        return stats

    for org_id in org_ids:
        stats["checked_orgs"] += 1
        health = evaluate_health(db, organization_id=org_id, now=now)
        state = _state_for(db, org_id)

        if not health["breached"]:
            if state.first_breached_at is not None or (state.breach_streak or 0) > 0:
                state.breach_streak = 0
                state.first_breached_at = None
                state.resolved_at = now
                stats["resolved_orgs"] += 1
                db.commit()
            continue

        if state.first_breached_at is None:
            state.first_breached_at = now
            state.breach_streak = 0
        state.last_breached_at = now
        state.breach_streak = (state.breach_streak or 0) + 1
        stats["breached_orgs"] += 1
        codes = [check["code"] for check in health["breached"]]
        stats["breached"].append({"organization_id": org_id, "codes": codes})

        admin_ids = _org_admin_ids(db, org_id)
        title = TITLE_TEMPLATE.format(count=len(health["breached"]))
        body = _alert_body(health["breached"], cooldown)
        events, muted = _notify(
            db, organization_id=org_id, admin_ids=admin_ids, title=title, body=body,
            template_key=TEMPLATE_KEY, business_version=window, now=now, escalate=False,
        )
        stats["notified_users"] += len(admin_ids)
        stats["events"] += events
        stats["quiet_users"] += muted
        state.last_alert_at = now

        # 持续越阈值 → 升级提醒（升级有独立冷却；不受静默时段限制）
        elapsed = now - state.first_breached_at
        if elapsed >= timedelta(minutes=settings.OUTBOX_ALERT_ESCALATE_AFTER_MINUTES):
            escalated_due = (
                state.escalated_at is None
                or (now - state.escalated_at) >= timedelta(minutes=escalation_cooldown)
            )
            if escalated_due:
                minutes = int(elapsed.total_seconds() // 60)
                summary = "；".join(
                    f"{check['label']} {check['value']} 条（阈值 {check['threshold']}）"
                    for check in health["breached"]
                )
                escalation_events, _ = _notify(
                    db, organization_id=org_id, admin_ids=admin_ids,
                    title=ESCALATION_TITLE_TEMPLATE.format(minutes=minutes),
                    body=f"投递异常已持续 {minutes} 分钟未恢复：{summary}。请立即处理。",
                    template_key=ESCALATION_TEMPLATE_KEY,
                    business_version=escalation_window, now=now, escalate=True,
                )
                state.escalated_at = now
                stats["escalated_orgs"] += 1
                stats["escalation_events"] += escalation_events
        db.commit()
    return stats
