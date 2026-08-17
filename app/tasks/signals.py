"""Celery 任务信号：为已注册关键任务写入 task_runs 台账。

未注册任务直接跳过（registry 查表，零开销）。信号内使用独立短会话，
任何异常仅记录日志，绝不阻断任务执行。软超时（SoftTimeLimitExceeded）
与普通失败一致走 ``task_failure``（error_code 记为异常类型名）。
"""

from __future__ import annotations

import logging

from celery import signals

from app.core.database import SessionLocal
from app.core.obs_context import build_context, context_from_headers, reset_context, set_context
from app.services.jobs.task_run_service import task_run_service
from app.tasks.task_run_registry import get_spec

logger = logging.getLogger(__name__)


def _error_fields(exc: Exception | None) -> tuple[str | None, str | None]:
    """稳定错误码 = 异常类型名；不落原始异常文本（可能含 URL/密钥），traceback 由任务日志承载。"""
    if exc is None:
        return None, None
    return type(exc).__name__, None


def _record_task_outcome(task, outcome: str, exc: Exception | None) -> None:
    """任务终态指标（P1）：task_name/outcome/error_category 均为有限枚举标签。"""
    if task is None:
        return
    try:
        from app.core.metrics import metrics
        from app.core.observability import classify_error_category

        metrics.increment(
            "task_outcomes",
            labels={
                "task_name": task.name,
                "outcome": outcome,
                "error_category": classify_error_category(exc) if exc is not None else "none",
            },
        )
    except Exception:  # noqa: BLE001 - 指标失败不影响任务
        pass


@signals.task_prerun.connect
def _on_task_prerun(task_id=None, task=None, args=None, kwargs=None, **kw):  # noqa: ANN001
    if task is None:
        return
    header_context = context_from_headers(getattr(task.request, "headers", None))
    context = build_context(**header_context, task_id=str(task_id) if task_id else None)
    set_context(context)
    spec = get_spec(task.name)
    if spec is None:
        return
    db = SessionLocal()
    try:
        retries = int(getattr(task.request, "retries", 0) or 0)
        call_args = args or ()
        context_fields = spec.context_fn(db, *call_args) if spec.context_fn else {}
        queue = getattr(task, "queue", None) or spec.queue
        task_run_service.start(
            db,
            task_id=task_id,
            task_name=task.name,
            queue=queue,
            business_key=spec.business_key_fn(*call_args),
            tenant_id=context_fields.get("tenant_id"),
            idempotency_key=spec.idempotency_key_fn(*call_args) if spec.idempotency_key_fn else None,
            max_attempts=spec.max_attempts,
            attempt=retries + 1,
            trace_id=context.trace_id,
            request_id=context.request_id,
            agent_run_id=context.agent_run_id,
        )
    except Exception:  # noqa: BLE001 - 台账失败不阻断任务
        logger.warning("task_run start failed for %s", task.name, exc_info=True)
    finally:
        db.close()


@signals.task_postrun.connect
def _on_task_postrun(**kw):  # noqa: ANN003
    """ContextVars must not leak between reused Celery worker processes."""
    reset_context()


@signals.task_success.connect
def _on_task_success(task_id=None, task=None, **kw):  # noqa: ANN001
    _record_task_outcome(task, "succeeded", None)
    if task is None or get_spec(task.name) is None:
        return
    db = SessionLocal()
    try:
        task_run_service.mark_succeeded(db, task_id=task_id)
    except Exception:  # noqa: BLE001
        logger.warning("task_run success failed for %s", task.name, exc_info=True)
    finally:
        db.close()


@signals.task_failure.connect
def _on_task_failure(task_id=None, task=None, exception=None, einfo=None, **kw):  # noqa: ANN001
    exc = exception if exception is not None else (einfo.exception if einfo is not None else None)
    _record_task_outcome(task, "failed", exc)
    if task is None or get_spec(task.name) is None:
        return
    db = SessionLocal()
    try:
        code, message = _error_fields(exc)
        task_run_service.mark_failed(db, task_id=task_id, error_code=code, error_message=message)
    except Exception:  # noqa: BLE001
        logger.warning("task_run failure failed for %s", task.name, exc_info=True)
    finally:
        db.close()


@signals.task_retry.connect
def _on_task_retry(task_id=None, task=None, request=None, einfo=None, **kw):  # noqa: ANN001
    exc = einfo.exception if einfo is not None else None
    _record_task_outcome(task, "retrying", exc)
    if task is None or get_spec(task.name) is None:
        return
    db = SessionLocal()
    try:
        retries = int(getattr(request, "retries", 0) or 0)
        eta = getattr(request, "eta", None)
        code, message = _error_fields(exc)
        task_run_service.mark_retrying(
            db,
            task_id=task_id,
            error_code=code,
            error_message=message,
            attempt=retries + 1,
            next_retry_at=eta,
        )
    except Exception:  # noqa: BLE001
        logger.warning("task_run retry failed for %s", task.name, exc_info=True)
    finally:
        db.close()
