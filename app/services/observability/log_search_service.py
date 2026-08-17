"""三轨日志集中检索（P1 API 统一化）：route 不直接操作 ORM，检索下沉 service。

keyword 模糊匹配下沉 SQL（LIKE + 通配符转义）；每源查询有界（500 行/源），
合并排序分页基于有界结果集——不进行全表加载。跨源合并无法做单条 UNION 分页，
故 total 为有界合并结果的长度（聚合视图语义，与 analytics alerts 一致）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

_QUERY_LIMIT_PER_SOURCE = 500


def _days_ago(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def _keyword_filter(columns, keyword: str | None):
    from sqlalchemy import or_

    if not keyword:
        return None
    escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return or_(*[column.like(f"%{escaped}%", escape="\\") for column in columns])


def search_logs(
    db: Session,
    *,
    source: str | None = None,
    keyword: str | None = None,
    action: str | None = None,
    module: str | None = None,
    user_id: int | None = None,
    days: int = 30,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    since = _days_ago(days)
    rows: list[dict] = []

    if source in (None, "operation_log"):
        from app.models.operation_log import OperationLog

        q = db.query(OperationLog).filter(OperationLog.created_at >= since)
        if action:
            q = q.filter(OperationLog.action == action)
        if module:
            q = q.filter(OperationLog.module == module)
        if user_id:
            q = q.filter(OperationLog.user_id == user_id)
        kw = _keyword_filter(
            [OperationLog.action, OperationLog.module, OperationLog.detail], keyword
        )
        if kw is not None:
            q = q.filter(kw)
        for r in q.order_by(OperationLog.created_at.desc()).limit(_QUERY_LIMIT_PER_SOURCE).all():
            rows.append({
                "source": "operation_log", "id": r.id, "user_id": r.user_id, "module": r.module,
                "action": r.action, "target_type": r.target_type, "target_id": r.target_id,
                "detail": r.detail, "ip_address": r.ip_address, "created_at": _iso(r.created_at),
            })

    if source in (None, "audit_log"):
        from app.models.auth_log import AdminAuditLog

        q = db.query(AdminAuditLog).filter(AdminAuditLog.created_at >= since)
        if action:
            q = q.filter(AdminAuditLog.action == action)
        if user_id:
            q = q.filter(AdminAuditLog.operator_id == user_id)
        kw = _keyword_filter(
            [AdminAuditLog.action, AdminAuditLog.operator_name,
             AdminAuditLog.target_name, AdminAuditLog.detail], keyword
        )
        if kw is not None:
            q = q.filter(kw)
        for r in q.order_by(AdminAuditLog.created_at.desc()).limit(_QUERY_LIMIT_PER_SOURCE).all():
            rows.append({
                "source": "audit_log", "id": r.id, "operator_id": r.operator_id,
                "operator_name": r.operator_name, "action": r.action,
                "target_type": r.target_type, "target_id": r.target_id, "target_name": r.target_name,
                "detail": r.detail, "ip_address": r.ip_address, "created_at": _iso(r.created_at),
            })

    if source in (None, "login_log"):
        from app.models.auth_log import LoginLog

        q = db.query(LoginLog).filter(LoginLog.created_at >= since)
        if action:
            q = q.filter(LoginLog.event_type == action)
        if user_id:
            q = q.filter(LoginLog.user_id == user_id)
        kw = _keyword_filter(
            [LoginLog.event_type, LoginLog.username, LoginLog.detail], keyword
        )
        if kw is not None:
            q = q.filter(kw)
        for r in q.order_by(LoginLog.created_at.desc()).limit(_QUERY_LIMIT_PER_SOURCE).all():
            rows.append({
                "source": "login_log", "id": r.id, "user_id": r.user_id, "username": r.username,
                "action": r.event_type, "target_type": None, "target_id": None,
                "detail": r.detail, "ip_address": r.ip_address, "created_at": _iso(r.created_at),
            })

    rows.sort(key=lambda x: x["created_at"] or "", reverse=True)
    total = len(rows)
    start = (page - 1) * page_size
    return {
        "items": rows[start:start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
