"""告警簇：告警分类、聚合统计与告警列表/统计查询。"""
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models.agent import AgentRun
from app.models.email import EmailSendRequest
from app.models.operation_log import OperationLog

ALERT_DATE_FORMAT = "%Y-%m-%d"

def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _classify_alert(
    *,
    source: str,
    title: str | None,
    message: str | None,
    target_type: str | None = None,
) -> dict:
    text = " ".join(part for part in [title, message, target_type] if part).lower()

    category = "system_error"
    error_type = "unknown_error"
    severity = "medium"

    if source == "outbound_email":
        if "approval_pending" in text:
            category = "approval_pending"
            error_type = "outbound_approval_pending"
            severity = "medium"
        else:
            category = "outbound_email_error"
            error_type = "smtp_delivery_failed"
            severity = "high"
    elif _contains_any(text, ("timeout", "timed out", "超时")):
        category = "timeout_error"
        error_type = "timeout"
        severity = "high"
    elif _contains_any(text, ("permission", "forbidden", "unauthorized", "403", "无权", "权限", "未授权")):
        category = "permission_error"
        error_type = "permission_denied"
        severity = "medium"
    elif _contains_any(text, ("network", "connection", "dns", "socket", "connect", "网络", "连接")):
        category = "network_error"
        error_type = "network_failure"
        severity = "high"
    elif _contains_any(text, ("openai", "model", "llm", "token", "context_length", "rate limit", "模型")):
        category = "model_error"
        error_type = "model_failure"
        severity = "high"
    elif _contains_any(text, ("tool", "工具", "observation", "action", "参数校验", "invalid params", "parameter")):
        category = "tool_error"
        error_type = "tool_execution_failed"
        severity = "high"
    elif _contains_any(
        text,
        (
            "not found",
            "不存在",
            "missing",
            "validation",
            "json",
            "parse",
            "schema",
            "empty",
            "null",
            "数据",
        ),
    ):
        category = "data_error"
        error_type = "data_validation_failed"
        severity = "medium"
    elif source == "agent":
        category = "agent_error"
        error_type = "agent_execution_failed"
        severity = "high"
    elif source == "async_task":
        category = "async_task_error"
        error_type = "async_task_failed"
        severity = "high"

    return {
        "category": category,
        "error_type": error_type,
        "severity": severity,
        "source_label": {
            "agent": "Agent",
            "async_task": "异步任务",
            "outbound_email": "外发邮件",
        }.get(source, source),
    }


def _build_alert_stats(alerts: list[dict]) -> dict:
    by_source: dict[str, int] = {}
    by_category: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    by_date: dict[str, int] = {}
    by_error_type: dict[str, int] = {}

    for alert in alerts:
        source = alert.get("source") or "unknown"
        category = alert.get("category") or "unknown"
        severity = alert.get("severity") or "unknown"
        error_type = alert.get("error_type") or "unknown"
        created_at = alert.get("created_at")
        date_key = created_at.strftime(ALERT_DATE_FORMAT) if created_at else "unknown"

        by_source[source] = by_source.get(source, 0) + 1
        by_category[category] = by_category.get(category, 0) + 1
        by_severity[severity] = by_severity.get(severity, 0) + 1
        by_error_type[error_type] = by_error_type.get(error_type, 0) + 1
        by_date[date_key] = by_date.get(date_key, 0) + 1

    return {
        "total": len(alerts),
        "by_source": by_source,
        "by_category": by_category,
        "by_severity": by_severity,
        "by_error_type": by_error_type,
        "by_date": by_date,
    }


class AlertsMixin:
    def list_alerts(
        self,
        db: Session,
        user_id: int | None = None,
        include_all_users: bool = False,
        days: int = 30,
        limit: int = 100,
        source: str | None = None,
        category: str | None = None,
        severity: str | None = None,
    ) -> list[dict]:
        since = utc_now() - timedelta(days=days)

        async_task_query = db.query(OperationLog).filter(
            OperationLog.module == "async_task",
            OperationLog.created_at >= since,
            OperationLog.action.like("%_failed"),
        )
        agent_query = db.query(AgentRun).filter(
            AgentRun.created_at >= since,
            AgentRun.status == "error",
        )

        if user_id is not None and not include_all_users:
            async_task_query = async_task_query.filter(OperationLog.user_id == user_id)
            agent_query = agent_query.filter(AgentRun.user_id == user_id)

        outbound_failure_query = db.query(EmailSendRequest).filter(
            EmailSendRequest.created_at >= since,
            EmailSendRequest.status == "failed",
        )
        outbound_pending_query = db.query(EmailSendRequest).filter(
            EmailSendRequest.created_at >= since,
            EmailSendRequest.created_at < utc_now() - timedelta(hours=24),
            EmailSendRequest.status == "pending",
        )
        if user_id is not None and not include_all_users:
            outbound_failure_query = outbound_failure_query.filter(EmailSendRequest.user_id == user_id)
            outbound_pending_query = outbound_pending_query.filter(EmailSendRequest.user_id == user_id)

        query_limit = max(limit * 5, 200)
        async_task_logs = async_task_query.order_by(OperationLog.created_at.desc()).limit(query_limit).all()
        agent_runs = agent_query.order_by(AgentRun.created_at.desc()).limit(query_limit).all()
        outbound_failures = outbound_failure_query.order_by(EmailSendRequest.created_at.desc()).limit(query_limit).all()
        outbound_pending = outbound_pending_query.order_by(EmailSendRequest.created_at.asc()).limit(query_limit).all()

        alerts: list[dict] = []
        for log in async_task_logs:
            alert = {
                "source": "async_task",
                "title": log.action,
                "message": log.detail or "异步任务执行失败",
                "user_id": log.user_id,
                "target_type": log.target_type,
                "target_id": log.target_id,
                "created_at": log.created_at,
            }
            alert.update(
                _classify_alert(
                    source=alert["source"],
                    title=alert["title"],
                    message=alert["message"],
                    target_type=alert["target_type"],
                )
            )
            alerts.append(
                alert
            )

        for run in agent_runs:
            alert = {
                "source": "agent",
                "title": "agent_run_failed",
                "message": run.failure_reason or run.error or run.goal[:120],
                "user_id": run.user_id,
                "target_type": "agent_run",
                "target_id": run.id,
                "created_at": run.created_at,
            }
            alert.update(
                _classify_alert(
                    source=alert["source"],
                    title=alert["title"],
                    message=alert["message"],
                    target_type=alert["target_type"],
                )
            )
            alerts.append(
                alert
            )

        for request in outbound_failures:
            alert = {
                "source": "outbound_email", "title": "smtp_delivery_failed",
                "message": "SMTP 发送失败，请检查发信连接与发送策略。",
                "user_id": request.user_id, "target_type": "email_send_request",
                "target_id": request.id, "created_at": request.updated_at or request.created_at,
            }
            alert.update(_classify_alert(source=alert["source"], title=alert["title"], message=alert["message"], target_type=alert["target_type"]))
            alerts.append(alert)

        for request in outbound_pending:
            alert = {
                "source": "outbound_email", "title": "outbound_approval_pending",
                "message": "发信申请已等待审批超过 24 小时。",
                "user_id": request.user_id, "target_type": "email_send_request",
                "target_id": request.id, "created_at": request.created_at,
            }
            alert.update(_classify_alert(source=alert["source"], title=alert["title"], message=alert["message"], target_type=alert["target_type"]))
            alerts.append(alert)

        if source:
            alerts = [item for item in alerts if item["source"] == source]
        if category:
            alerts = [item for item in alerts if item["category"] == category]
        if severity:
            alerts = [item for item in alerts if item["severity"] == severity]

        alerts.sort(key=lambda item: item["created_at"] or datetime.min, reverse=True)
        return alerts[:limit]

    def get_alert_stats(
        self,
        db: Session,
        user_id: int | None = None,
        include_all_users: bool = False,
        days: int = 30,
        source: str | None = None,
        category: str | None = None,
        severity: str | None = None,
    ) -> dict:
        alerts = self.list_alerts(
            db=db,
            user_id=user_id,
            include_all_users=include_all_users,
            days=days,
            limit=1000,
            source=source,
            category=category,
            severity=severity,
        )
        return _build_alert_stats(alerts)

