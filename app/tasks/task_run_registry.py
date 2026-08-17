"""关键任务的运行台账注册表：opt-in 登记后由信号自动记账。

未登记的任务在信号层零开销跳过。每条 spec 提供从任务参数推导
``business_key``（同业务对象多次运行的可比状态）、可选 ``context_fn``
（从参数解析 tenant_id/user_id，供租户范围查询）与队列归属。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

# business_key_fn: (*args) -> business_key ｜ None
BusinessKeyFn = Callable[..., str | None]
# context_fn: (db, *args) -> {"tenant_id"?, "user_id"?}，返回空 dict 表示无法归属
ContextFn = Callable[..., dict]


@dataclass(frozen=True)
class TaskRunSpec:
    task_name: str
    queue: str
    business_key_fn: BusinessKeyFn
    context_fn: ContextFn | None = None
    idempotency_key_fn: BusinessKeyFn | None = None
    max_attempts: int | None = None


_TASK_SPECS: dict[str, TaskRunSpec] = {}


def register(spec: TaskRunSpec) -> None:
    _TASK_SPECS[spec.task_name] = spec


def get_spec(task_name: str) -> TaskRunSpec | None:
    return _TASK_SPECS.get(task_name)


def all_specs() -> list[TaskRunSpec]:
    return list(_TASK_SPECS.values())


# ── 文档流水线（同一 document 共享 business_key，便于"新版本取代旧重试"）─────

def _document_key(document_id: int, *_: Any) -> str:
    return f"document:{int(document_id)}"


def _document_context(db: Any, document_id: int, *_: Any) -> dict:
    from app.models.document import Document

    doc = db.query(Document).filter(Document.id == int(document_id)).first()
    if not doc:
        return {}
    return {"tenant_id": doc.organization_id, "user_id": doc.user_id}


# summarize/analyze 的第二个参数是 user_id（organization_id 需查 User）
def _user_context_at(index: int) -> ContextFn:
    def fn(db: Any, *args: Any) -> dict:
        if len(args) <= index:
            return {}
        from app.models.user import User

        user = db.query(User).filter(User.id == int(args[index])).first()
        if not user:
            return {}
        return {"tenant_id": user.organization_id, "user_id": user.id}

    return fn


def _async_job_key(job_id: int, *_: Any) -> str:
    return f"legal_async_job:{int(job_id)}"


def _connector_key(connector_id: int, *_: Any) -> str:
    return f"connector:{int(connector_id)}"


for _name, _queue, _bf, _cf, _idem in [
    ("parse_document", "document", _document_key, _document_context, None),
    ("document_chunk", "document", _document_key, _document_context, None),
    ("document_index", "document", _document_key, _document_context, None),
    ("summarize_document", "llm", _document_key, _user_context_at(1), None),
    ("analyze_document", "llm", _document_key, _user_context_at(1), None),
    ("process_open_contract_review", "llm", _async_job_key, None, None),
    ("parse_contract_versions", "document", lambda *_: None, None, None),
    ("run_database_archive", "connector", lambda *_: None, None, None),
    ("create_pilot_backup", "connector", lambda *_: None, None, None),
    # P1 可观测性任务（台账便于回溯聚合/快照/导出运行历史）
    ("snapshot_ops_metrics", "document", lambda *_: None, None, None),
    ("aggregate_ops_metrics", "document", lambda *_: None, None, None),
    ("run_audit_export", "document", _async_job_key, None, None),
]:
    register(TaskRunSpec(task_name=_name, queue=_queue, business_key_fn=_bf, context_fn=_cf, idempotency_key_fn=_idem))

# connector_sync_task 在 app/tasks/__init__.py 中定义后由同文件 register（保持单一来源）。
