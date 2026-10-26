"""Outbox 投递告警：按组织评估阈值，超限时给组织管理员发站内 + 邮件通知。

为什么按组织而不是全局：
- 通知事件与邮件请求都有 ``organization_id``，租户隔离下「A 组织积压」不该吵到 B 组织；
- 平台管理员（``organization_id IS NULL``）不接收组织级事件（通知表要求 org 非空），
  这类账号只能在投递看板看全局视图——文档里明确写了这条边界。

幂等/冷却：
- 同一组织、同一用户、同一渠道在一个冷却窗口内只发一条：
  ``business_version`` 取 ``now // OUTBOX_ALERT_COOLDOWN_MINUTES``，幂等键天然去重；
- beat 每 15 分钟跑一次，冷却默认 180 分钟 → 同一条告警最多每 3 小时一次。
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.time import utc_now
from app.models.user import User
from app.services.notification.delivery_ops import evaluate_health
from app.services.notification.notification_service import notification_service

EVENT_TYPE = "approval"
TEMPLATE_KEY = "outbox_health_alert"
REFERENCE_TYPE = "outbox_health"
TITLE_TEMPLATE = "投递告警：{count} 项超阈值"

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


def _alert_body(breached: list[dict], cooldown_minutes: int) -> str:
    parts = [
        f"{check['label']} {check['value']} 条（阈值 {check['threshold']}）"
        for check in breached
    ]
    return f"{'；'.join(parts)}。请到「系统 → 投递看板」处理；同类告警 {cooldown_minutes} 分钟内不重复发送。"


def run_outbox_health_check(db: Session, *, now: datetime | None = None) -> dict:
    """扫描有管理员的组织，超阈值时发告警；返回本次扫描统计（不落业务表）。"""
    settings = get_settings()
    now = now or utc_now()
    cooldown = settings.OUTBOX_ALERT_COOLDOWN_MINUTES
    window = int(now.timestamp() // (cooldown * 60))

    stats = {
        "checked_orgs": 0,
        "breached_orgs": 0,
        "notified_users": 0,
        "events": 0,
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
        if not health["breached"]:
            continue
        stats["breached_orgs"] += 1
        codes = [check["code"] for check in health["breached"]]
        stats["breached"].append({"organization_id": org_id, "codes": codes})

        title = TITLE_TEMPLATE.format(count=len(health["breached"]))
        body = _alert_body(health["breached"], cooldown)
        for user_id in _org_admin_ids(db, org_id):
            events = notification_service.create_multi_channel_notification(
                db=db,
                organization_id=org_id,
                user_id=user_id,
                event_type=EVENT_TYPE,
                title=title,
                body=body,
                channels=["site", "email"],
                reference_type=REFERENCE_TYPE,
                reference_id=org_id,
                # 冷却窗口进幂等键：窗口内重复扫描不会再产生新事件
                business_version=window,
                template_key=TEMPLATE_KEY,
            )
            stats["notified_users"] += 1
            stats["events"] += len(events)
    return stats
