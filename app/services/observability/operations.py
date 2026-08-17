"""运维与任务簇：操作日志、任务运行列表/重试。"""
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.celery_app import celery_app
from app.core.obs_context import enqueue_headers as obs_enqueue_headers
from app.core.time import utc_now
from app.models.agent import AgentRun
from app.models.document import Document
from app.models.operation_log import OperationLog
from app.services.observability.analytics_task_state import (
    extract_max_length,
    extract_task_id,
    normalize_async_state,
)


def _task_title_from_action(action: str | None, target_type: str | None) -> str:
    mapping = {
        "document_parse": "文档解析",
        "document_summary": "文档摘要",
        "document_analysis": "文档分析",
    }
    action = action or ""
    for prefix, label in mapping.items():
        if action.startswith(prefix):
            return label
    return f"{target_type or '任务'}执行"


class OperationsMixin:
    def list_operation_logs(
        self,
        db: Session,
        user_id: int | None = None,
        module: str | None = None,
        include_all_users: bool = False,
        days: int = 30,
        limit: int = 200,
        page: int | None = None,
        page_size: int | None = None,
    ) -> list[OperationLog] | tuple[list[OperationLog], int]:
        since = utc_now() - timedelta(days=days)
        query = db.query(OperationLog).filter(OperationLog.created_at >= since)
        if user_id is not None and not include_all_users:
            query = query.filter(OperationLog.user_id == user_id)
        if module:
            query = query.filter(OperationLog.module == module)
        ordered = query.order_by(OperationLog.created_at.desc(), OperationLog.id.desc())
        if page is not None and page_size is not None:
            total = query.count()
            items = ordered.offset((page - 1) * page_size).limit(page_size).all()
            return items, total
        return ordered.limit(limit).all()

    def get_operation_stats(self, user_id: int, db: Session, days: int = 30) -> dict:
        since = utc_now() - timedelta(days=days)
        rows = db.query(OperationLog).filter(
            OperationLog.user_id == user_id,
            OperationLog.created_at >= since,
        ).all()

        by_module = {}
        for row in rows:
            key = row.module
            if key not in by_module:
                by_module[key] = 0
            by_module[key] += 1

        return {
            "total_operations": len(rows),
            "by_module": by_module,
        }

    def create_operation_log(
        self,
        module: str,
        action: str,
        db: Session,
        user_id: int | None = None,
        target_type: str | None = None,
        target_id: int | None = None,
        detail: str | None = None,
        ip_address: str | None = None,
    ) -> OperationLog:
        entry = OperationLog(
            user_id=user_id,
            module=module,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
            ip_address=ip_address,
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return entry

    def list_task_runs(
        self,
        db: Session,
        *,
        user_id: int | None = None,
        include_all_users: bool = False,
        days: int = 30,
        limit: int = 100,
        source: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        since = utc_now() - timedelta(days=days)
        items: list[dict] = []

        if source in (None, "async_task"):
            query = db.query(OperationLog).filter(
                OperationLog.module == "async_task",
                OperationLog.created_at >= since,
            )
            if user_id is not None and not include_all_users:
                query = query.filter(OperationLog.user_id == user_id)

            logs = query.order_by(OperationLog.created_at.desc()).limit(limit * 10).all()
            grouped: dict[str, dict] = {}

            for log in logs:
                task_id = extract_task_id(log.detail)
                if not task_id:
                    continue
                entry = grouped.get(task_id)
                if not entry:
                    result = celery_app.AsyncResult(task_id)
                    celery_state = result.state
                    normalized_status = normalize_async_state(celery_state, log.action)
                    detail = log.detail or ""
                    entry = {
                        "task_key": task_id,
                        "source": "async_task",
                        "task_type": log.action.rsplit("_", 1)[0],
                        "title": _task_title_from_action(log.action, log.target_type),
                        "status": normalized_status,
                        "celery_state": celery_state,
                        "module": "async_task",
                        "target_type": log.target_type,
                        "target_id": log.target_id,
                        "user_id": log.user_id,
                        "message": detail,
                        "error": str(result.info) if result.failed() else None,
                        "retryable": normalized_status == "failed",
                        "created_at": log.created_at,
                        "updated_at": log.created_at,
                        "events": [],
                    }
                    if result.failed() and not entry["error"]:
                        entry["error"] = detail
                    if result.successful() and isinstance(result.info, dict):
                        entry["result"] = result.info
                    grouped[task_id] = entry

                entry["events"].append(
                    {
                        "id": log.id,
                        "action": log.action,
                        "detail": log.detail,
                        "created_at": log.created_at,
                    }
                )

                if log.created_at and log.created_at > entry["updated_at"]:
                    entry["updated_at"] = log.created_at
                    entry["message"] = log.detail or entry["message"]

                action_status = normalize_async_state(None, log.action)
                if action_status == "failed":
                    entry["status"] = "failed"
                    entry["error"] = log.detail or entry["error"]
                elif entry["status"] not in {"failed", "succeeded"}:
                    entry["status"] = action_status

            items.extend(grouped.values())

        if source in (None, "agent"):
            query = db.query(AgentRun).filter(AgentRun.created_at >= since)
            if user_id is not None and not include_all_users:
                query = query.filter(AgentRun.user_id == user_id)
            runs = query.order_by(AgentRun.created_at.desc()).limit(limit).all()
            for run in runs:
                normalized_status = {
                    "running": "running",
                    "completed": "succeeded",
                    "error": "failed",
                }.get(run.status, run.status or "pending")
                items.append(
                    {
                        "task_key": str(run.id),
                        "source": "agent",
                        "task_type": "agent_run",
                        "title": "Agent 执行",
                        "status": normalized_status,
                        "celery_state": None,
                        "module": "agent",
                        "target_type": "agent_run",
                        "target_id": run.id,
                        "user_id": run.user_id,
                        "message": run.final_answer or run.result or run.goal,
                        "error": run.failure_reason or run.error,
                        "retryable": normalized_status == "failed",
                        "created_at": run.created_at,
                        "updated_at": run.completed_at or run.created_at,
                        "goal": run.goal,
                        "total_steps": run.total_steps,
                    }
                )

        if status:
            items = [item for item in items if item["status"] == status]

        items.sort(key=lambda item: item["updated_at"] or item["created_at"] or datetime.min, reverse=True)
        return items[:limit]

    def retry_task_run(
        self,
        db: Session,
        *,
        source: str,
        task_key: str,
        user_id: int,
    ) -> dict:
        if source == "agent":
            run = db.query(AgentRun).filter(AgentRun.id == int(task_key), AgentRun.user_id == user_id).first()
            if not run:
                raise ValueError("Agent run not found")
            return {
                "source": "agent",
                "task_key": task_key,
                "goal": run.goal,
                "max_steps": run.total_steps or 5,
            }

        if source != "async_task":
            raise ValueError("Unsupported task source")

        logs = (
            db.query(OperationLog)
            .filter(
                OperationLog.module == "async_task",
                OperationLog.user_id == user_id,
                OperationLog.detail.like(f"%task_id={task_key}%"),
            )
            .order_by(OperationLog.created_at.desc())
            .all()
        )
        if not logs:
            raise ValueError("Async task not found")

        latest = logs[0]
        action = latest.action or ""
        target_type = latest.target_type
        target_id = latest.target_id
        max_length = extract_max_length(latest.detail)

        # 懒加载任务函数：避免 analytics_service 模块级 import app.tasks（打破循环依赖）。
        from app.tasks import analyze_document_task, parse_document_task, summarize_document_task

        if action.startswith("document_analysis"):
            task = analyze_document_task.delay(int(target_id), user_id, max_length, headers=obs_enqueue_headers())
        elif action.startswith("document_summary"):
            task = summarize_document_task.delay(int(target_id), user_id, max_length, headers=obs_enqueue_headers())
        elif action.startswith("document_parse"):
            doc = db.query(Document).filter(Document.id == int(target_id), Document.user_id == user_id).first()
            if not doc:
                raise ValueError("Document not found")
            task = parse_document_task.delay(doc.id, doc.version_number, doc.file_type, headers=obs_enqueue_headers())
        else:
            raise ValueError("Task type is not retryable")

        self.create_operation_log(
            module="async_task",
            action=f"{action.rsplit('_', 1)[0]}_submitted",
            db=db,
            user_id=user_id,
            target_type=target_type,
            target_id=target_id,
            detail=f"task_id={task.id}; retry_of={task_key}; max_length={max_length}",
        )

        return {
            "source": "async_task",
            "task_key": task.id,
            "retry_of": task_key,
            "target_type": target_type,
            "target_id": target_id,
        }

