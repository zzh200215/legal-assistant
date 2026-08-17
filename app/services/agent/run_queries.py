from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.mcp.permissions import (
    CANONICAL_AGENT_TYPES,
    canonical_agent_type,
)
from app.models.agent import AgentRun, ToolCallLog
from app.services.agent.agent_approval_service import agent_approval_service
from app.services.agent.agent_audit import (
    EVENT_CANCEL,
    agent_audit_service,
)
from app.services.agent.agent_json import json_loads_dict as _json_loads_dict
from app.services.agent.agent_prompts import sanitize_agent_error_message as _sanitize_agent_error_message
from app.services.agent.agent_run_state import RunStateMachine


class RunQueriesMixin:
    def get_run(self, run_id: int, db: Session, user_id: int | None = None) -> AgentRun | None:
        return self._repo.get_run(db, run_id, user_id=user_id)

    def request_cancel(self, run_id: int, *, db: Session, user_id: int, reason: str | None = None) -> AgentRun:
        run = self.get_run(run_id, db, user_id=user_id)
        if not run:
            raise ValueError("Agent run not found")
        if not RunStateMachine.can_cancel(run.status):
            raise ValueError("Agent run is not active")
        run.cancel_requested_at = utc_now()
        run.cancel_reason = (reason or "").strip() or None
        if run.status == "awaiting_approval":
            run.status = RunStateMachine.transition(run.status, "cancelled")
            run.final_answer = "执行已取消，未恢复待审批操作。"
            run.failure_reason = "cancelled_by_user"
            run.completed_at = utc_now()
        else:
            run.status = RunStateMachine.transition(run.status, "cancelling")
        result = self._save_run(db, run)
        try:
            agent_audit_service.record(
                db=db, event_type=EVENT_CANCEL, run_id=run.id, user_id=user_id,
                summary={"reason": reason}, status="cancelled" if run.status == "cancelled" else "cancelling",
            )
        except Exception:  # noqa: BLE001
            db.rollback()
        return result

    def get_run_metrics(self, *, db: Session, user_id: int, days: int = 30) -> dict[str, Any]:
        since = utc_now() - timedelta(days=max(1, min(days, 365)))
        runs = db.query(AgentRun).filter(AgentRun.user_id == user_id, AgentRun.created_at >= since).all()
        completed = [item for item in runs if item.status == "completed"]
        terminal = [item for item in runs if item.status in {"completed", "error", "cancelled"}]
        durations = [int((item.completed_at - item.created_at).total_seconds() * 1000) for item in completed if item.completed_at and item.created_at]
        approvals = [
            item
            for item in agent_approval_service.list_requests(db=db, user_id=user_id)
            if item.created_at and item.created_at >= since
        ]
        decided = [item for item in approvals if item.status in {"approved", "executed", "rejected"}]
        approved = [item for item in decided if item.status in {"approved", "executed"}]
        role_stats: dict[str, dict[str, Any]] = {
            role: {
                "agent_type": role,
                "planned_runs": 0,
                "terminal_runs": 0,
                "completed_runs": 0,
                "tool_calls": 0,
                "tool_success_calls": 0,
                "tool_failed_calls": 0,
                "pending_approval_calls": 0,
                "retry_count": 0,
                "total_tool_duration_ms": 0,
            }
            for role in CANONICAL_AGENT_TYPES
        }
        for run in runs:
            payload = _json_loads_dict(run.result)
            plan = payload.get("supervisor_plan") if isinstance(payload.get("supervisor_plan"), dict) else {}
            workers = plan.get("workers") if isinstance(plan.get("workers"), list) else []
            for worker in {canonical_agent_type(str(item)) for item in workers}:
                if worker not in role_stats:
                    continue
                role_stats[worker]["planned_runs"] += 1
                if run.status in {"completed", "error", "cancelled"}:
                    role_stats[worker]["terminal_runs"] += 1
                if run.status == "completed":
                    role_stats[worker]["completed_runs"] += 1

        logs = (
            db.query(ToolCallLog)
            .join(AgentRun)
            .filter(AgentRun.user_id == user_id, AgentRun.created_at >= since)
            .all()
        )
        control_logs = {
            "finish",
            "evidence_verifier",
            "supervisor_handoff",
            "supervisor_parallel_fanout",
            "supervisor_aggregate",
            "run_cancelled",
        }
        terminal_tool_logs = 0
        successful_tool_logs = 0
        retrying_runs: set[int] = set()
        for log in logs:
            input_params = _json_loads_dict(log.input_params)
            agent_type = canonical_agent_type(str(input_params.get("_worker_agent") or ""))
            if agent_type not in role_stats:
                continue
            stat = role_stats[agent_type]
            if log.tool_name == "retry":
                stat["retry_count"] += 1
                retrying_runs.add(log.agent_run_id)
                continue
            if log.tool_name in control_logs or log.status == "approved":
                continue
            if log.status == "pending_approval":
                stat["pending_approval_calls"] += 1
                continue
            stat["tool_calls"] += 1
            stat["total_tool_duration_ms"] += int(log.duration_ms or 0)
            if log.status == "success":
                stat["tool_success_calls"] += 1
                terminal_tool_logs += 1
                successful_tool_logs += 1
            else:
                stat["tool_failed_calls"] += 1
                terminal_tool_logs += 1

        by_agent = []
        for role in CANONICAL_AGENT_TYPES:
            stat = role_stats[role]
            tool_attempts = stat["tool_success_calls"] + stat["tool_failed_calls"]
            stat["run_success_rate"] = (
                round(stat["completed_runs"] / stat["terminal_runs"], 4) if stat["terminal_runs"] else None
            )
            stat["tool_success_rate"] = (
                round(stat["tool_success_calls"] / tool_attempts, 4) if tool_attempts else None
            )
            stat["average_tool_duration_ms"] = (
                round(stat["total_tool_duration_ms"] / stat["tool_calls"]) if stat["tool_calls"] else None
            )
            stat.pop("total_tool_duration_ms", None)
            by_agent.append(stat)

        total_tool_calls = sum(item["tool_calls"] for item in by_agent)
        return {
            "days": days,
            "total_runs": len(runs),
            "success_rate": round(len(completed) / len(terminal), 4) if terminal else None,
            "cancelled_runs": sum(item.status == "cancelled" for item in runs),
            "average_duration_ms": round(sum(durations) / len(durations)) if durations else None,
            "approval_count": len(approvals),
            "approval_rate": round(len(approved) / len(decided), 4) if decided else None,
            "reliability": {
                "tool_success_rate": round(successful_tool_logs / terminal_tool_logs, 4) if terminal_tool_logs else None,
                "retrying_run_count": len(retrying_runs),
                "retrying_run_rate": round(len(retrying_runs) / len(runs), 4) if runs else None,
                "average_tool_calls_per_run": round(total_tool_calls / len(runs), 2) if runs else 0,
                "human_intervention_rate": round(len(approvals) / len(runs), 4) if runs else None,
                "routing_accuracy": None,
                "routing_accuracy_note": "当前无人工标注的意图真值集，路由准确率不自动估算。",
            },
            "by_agent": by_agent,
        }

    def get_run_logs(self, run_id: int, db: Session, user_id: int | None = None) -> list[ToolCallLog]:
        return self._repo.get_run_logs(db, run_id, user_id=user_id)

    def list_runs_by_artifact(
        self,
        *,
        db: Session,
        user_id: int,
        artifact_type: str,
        artifact_id: int,
        page: int | None = None,
        page_size: int | None = None,
    ) -> list[AgentRun] | tuple[list[AgentRun], int]:
        """按产出对象过滤运行记录。artifacts 存于 result JSON，用带后缀的
        LIKE 子串下沉 SQL（``"document_id": N,`` / ``"document_id": N}`` 精确匹配），
        分页与 count 全部走数据库，避免全量加载后内存过滤。
        """
        from sqlalchemy import or_

        type_mapping = {
            "document": ("documents", "document_id"),
            "task": ("tasks", "task_id"),
        }
        artifact_key = type_mapping.get((artifact_type or "").strip().lower())
        if not artifact_key:
            return ([], 0) if page is not None else []

        bucket_name, id_field = artifact_key
        q = (
            db.query(AgentRun)
            .filter(AgentRun.user_id == user_id)
            .filter(or_(
                AgentRun.result.like(f'%"{id_field}": {artifact_id},%'),
                AgentRun.result.like(f'%"{id_field}": {artifact_id}}}%'),
            ))
        )
        ordered = q.order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        if page is not None and page_size is not None:
            total = q.count()
            runs = ordered.offset((page - 1) * page_size).limit(page_size).all()
            # LIKE 兜底二次精确确认（排除极端 JSON 边界），确认失败的行仅用于剔除。
            matched = [
                run for run in runs
                if any(
                    isinstance(row, dict) and int(row.get(id_field) or 0) == artifact_id
                    for row in ((self.serialize_run(run).get("artifacts") or {}).get(bucket_name) or [])
                    if isinstance(row, dict)
                )
            ]
            return matched, total
        return ordered.all()

    def serialize_log(self, log: ToolCallLog) -> dict[str, Any]:
        return {
            "id": log.id,
            "agent_run_id": log.agent_run_id,
            "step": log.step,
            "action_type": log.action_type,
            "thought": log.thought,
            "tool_name": log.tool_name,
            "input_params": log.input_params,
            "raw_decision": log.raw_decision,
            "observation": log.observation,
            "output_result": log.output_result,
            "status": log.status,
            "error": _sanitize_agent_error_message(log.error),
            "duration_ms": log.duration_ms,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }

    def serialize_run(self, run: AgentRun) -> dict[str, Any]:
        parsed_result = _json_loads_dict(run.result)
        return {
            "id": run.id,
            "user_id": run.user_id,
            "session_id": run.session_id,
            "goal": run.goal,
            "status": run.status,
            "result": run.result,
            "final_answer": run.final_answer,
            "artifacts": parsed_result.get("artifacts") if isinstance(parsed_result.get("artifacts"), dict) else {},
            "supervisor_plan": parsed_result.get("supervisor_plan") if isinstance(parsed_result.get("supervisor_plan"), dict) else {},
            "last_observation": run.last_observation,
            "failure_reason": _sanitize_agent_error_message(run.failure_reason),
            "total_steps": run.total_steps,
            "error": _sanitize_agent_error_message(run.error),
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "cancel_requested_at": run.cancel_requested_at.isoformat() if run.cancel_requested_at else None,
            "cancel_reason": run.cancel_reason,
        }
