"""产品漏斗埋点服务（ux-audit 需补充信息：注册→首个任务完成漏斗无法量化）。

设计原则：
1. 服务端埋点——漏斗关键动作都发生在后端端点（注册/登录/建案/咨询/审核），
   服务端记录比前端 JS 更可靠（不受网络/隐私插件/页面卸载影响）；
2. 绝不影响主流程——record_event 内部吞掉一切异常，只 logging.warning；
3. 只记元数据不记业务内容（不含问题/案情文本），符合法律数据最小化收集。
门户访问不在此记录：legal_portal_access_logs 已有完整数据，汇总时合并。
"""
import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.analytics_funnel import FUNNEL_EVENTS, AnalyticsFunnelEvent
from app.models.legal_portal import LegalPortalAccessLog

logger = logging.getLogger(__name__)


def record_event(
    db: Session,
    event: str,
    *,
    user_id: int | None = None,
    organization_id: int | None = None,
    case_id: int | None = None,
    path: str | None = None,
    props: dict | None = None,
) -> None:
    """记录一个漏斗事件。任何失败只告警不抛出（埋点永不阻断业务）。"""
    if event not in FUNNEL_EVENTS:
        logger.warning("[funnel] unknown event skipped: %s", event)
        return
    try:
        # SAVEPOINT 隔离：埋点 flush 失败只回滚到保存点，绝不影响调用方未提交的业务数据
        with db.begin_nested():
            db.add(AnalyticsFunnelEvent(
                event=event,
                user_id=user_id,
                organization_id=organization_id,
                case_id=case_id,
                path=path,
                props_json=json.dumps(props, ensure_ascii=False) if props else None,
            ))
            db.flush()
    except Exception:  # noqa: BLE001 — 埋点失败绝不影响主流程
        logger.warning("[funnel] record_event failed for %s", event, exc_info=True)


def funnel_summary(db: Session, days: int = 30) -> dict:
    """漏斗汇总：各事件总量/去重用户数/按日序列，附门户访问量（复用 access_logs）。"""
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
    rows = (
        db.query(
            AnalyticsFunnelEvent.event,
            func.count(AnalyticsFunnelEvent.id),
            func.count(func.distinct(AnalyticsFunnelEvent.user_id)),
            func.min(AnalyticsFunnelEvent.created_at),
        )
        .filter(AnalyticsFunnelEvent.created_at >= since)
        .group_by(AnalyticsFunnelEvent.event)
        .all()
    )
    totals = {
        row[0]: {"total": row[1], "unique_users": row[2], "first_at": row[3].isoformat() if row[3] else None}
        for row in rows
    }

    daily_rows = (
        db.query(
            AnalyticsFunnelEvent.event,
            func.date(AnalyticsFunnelEvent.created_at),
            func.count(AnalyticsFunnelEvent.id),
        )
        .filter(AnalyticsFunnelEvent.created_at >= since)
        .group_by(AnalyticsFunnelEvent.event, func.date(AnalyticsFunnelEvent.created_at))
        .all()
    )
    by_day: dict[str, list[dict]] = {event: [] for event in FUNNEL_EVENTS}
    for event, date, count in daily_rows:
        by_day.setdefault(event, []).append({"date": str(date), "count": count})

    portal_rows = (
        db.query(
            func.count(LegalPortalAccessLog.id),
            func.date(LegalPortalAccessLog.accessed_at),
        )
        .filter(LegalPortalAccessLog.accessed_at >= since)
        .group_by(func.date(LegalPortalAccessLog.accessed_at))
        .all()
    )
    portal_by_day = [{"date": str(date), "count": count} for count, date in portal_rows]

    return {
        "days": days,
        "events": {
            event: {
                **totals.get(event, {"total": 0, "unique_users": 0, "first_at": None}),
                "by_day": by_day.get(event, []),
            }
            for event in FUNNEL_EVENTS
        },
        "portal_visits": {
            "total": sum(item["count"] for item in portal_by_day),
            "by_day": portal_by_day,
        },
    }
