"""补偿协调器：run 失败后按反向顺序对已成功执行的写步骤执行补偿。

- 仅补偿声明 ``compensable=True`` 且有补偿处理器的写工具。
- 不可补偿步骤明确记录 ``compensation_status=not_compensable``，不静默跳过。
- 补偿处理器从工具结果中提取实体引用（如 task.id），不依赖模型猜测。
- 审计事件记录每步补偿决策与状态。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.agent import AgentRun, ToolCallLog
from app.services.agent_audit import EVENT_COMPENSATION, agent_audit_service
from app.services.agent_json import json_loads_dict as _json_loads_dict
from app.mcp.tool_contract import resolve_contract


def _registry_lookup(tool_name: str):
    """按工具名查工具实例（供契约判定读/写）。"""
    try:
        from app.mcp.registry import mcp_registry

        return mcp_registry.get_tool(tool_name)
    except Exception:  # noqa: BLE001
        return None

# 补偿处理器注册表：tool_name -> handler(tool_name, result_ref, user_id, db)
_COMPENSATORS: dict[str, Any] = {}


def register_compensator(tool_name: str, handler) -> None:
    _COMPENSATORS[tool_name] = handler


def compensate_task(tool_name: str, result_ref: dict[str, Any] | None, user_id: int, db: Session) -> None:
    """把 task_create_tool 创建的关联任务标记为 cancelled。"""
    if not result_ref:
        raise ValueError("缺少任务引用，无法补偿")
    from app.services.task_service import task_service

    task_id = int(result_ref.get("id") or 0)
    if not task_id:
        raise ValueError("缺少任务 ID，无法补偿")
    task_service.update(task_id=task_id, db=db, user_id=user_id, status="cancelled")


def _extract_result_ref(log: ToolCallLog) -> dict[str, Any] | None:
    observation = _json_loads_dict(log.observation)
    data = observation.get("data") if isinstance(observation.get("data"), dict) else {}
    task = data.get("task") if isinstance(data.get("task"), dict) else None
    if task and isinstance(task.get("id"), int):
        return {"kind": "task", "id": task["id"]}
    return None


def _audit_compensation(
    db: Session,
    run: AgentRun,
    *,
    log: ToolCallLog | None,
    tool_name: str,
    status: str,
    detail: str,
    user_id: int | None,
) -> None:
    try:
        agent_audit_service.record(
            db=db,
            event_type=EVENT_COMPENSATION,
            run_id=run.id,
            step=log.step if log else None,
            trace_id=run.trace_id,
            user_id=user_id or run.user_id,
            organization_id=run.organization_id,
            tool_name=tool_name,
            status=status,
            summary={"compensation_status": status, "detail": detail},
        )
    except Exception:  # noqa: BLE001 - 审计失败不回滚补偿
        db.rollback()


def run_compensation(db: Session, run: AgentRun) -> dict[str, Any]:
    """run 失败后执行补偿。返回每个已成功写步骤的补偿记录。"""
    logs = (
        db.query(ToolCallLog)
        .filter(ToolCallLog.agent_run_id == run.id)
        .order_by(ToolCallLog.step.asc(), ToolCallLog.id.asc())
        .all()
    )
    completed_writes = []
    for log in logs:
        if log.status != "success":
            continue
        tool = _registry_lookup(log.tool_name)
        contract = resolve_contract(tool)
        if contract.read_only:
            continue
        completed_writes.append(log)

    records: list[dict[str, Any]] = []
    for log in reversed(completed_writes):  # 反向顺序补偿
        handler = _COMPENSATORS.get(log.tool_name)
        if handler is None:
            _audit_compensation(
                db, run, log=log, tool_name=log.tool_name, status="not_compensable",
                detail="写工具未声明补偿处理器", user_id=run.user_id,
            )
            records.append({"tool_name": log.tool_name, "step": log.step, "compensation_status": "not_compensable"})
            continue
        result_ref = _extract_result_ref(log)
        try:
            handler(log.tool_name, result_ref, run.user_id, db)
            status = "compensated"
            detail = f"已撤销 {result_ref.get('kind', 'resource')}#{result_ref.get('id')}" if result_ref else "已执行补偿"
        except Exception as exc:  # noqa: BLE001 - 补偿失败需记录审计
            status = "compensation_failed"
            detail = str(exc)
        _audit_compensation(
            db, run, log=log, tool_name=log.tool_name, status=status, detail=detail, user_id=run.user_id,
        )
        records.append({"tool_name": log.tool_name, "step": log.step, "compensation_status": status})

    if not records:
        overall = "none"
    elif all(item["compensation_status"] == "compensated" for item in records):
        overall = "completed"
    else:
        overall = "partial"
    run.compensation_status = overall
    db.add(run)
    db.commit()
    return {"compensation_status": overall, "records": records}


# 注册内置补偿器
register_compensator("task_create_tool", compensate_task)
