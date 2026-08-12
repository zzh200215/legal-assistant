import asyncio
import json
import re
import time
from datetime import timedelta
from typing import Any, Awaitable, Callable

from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.model_policy import new_trace_id
from app.core.time import utc_now
from app.services.agent_approval_service import agent_approval_service
from app.mcp.executor import tool_executor
from app.mcp.permission_guard import permission_guard
from app.mcp.permissions import (
    CANONICAL_AGENT_TYPES,
    agent_allows_tool,
    allowed_tools_for,
    canonical_agent_type,
)
from app.mcp.registry import mcp_registry
from app.models.agent import AgentRun, ToolCallLog
from app.models.user import User
from app.services.agent_planner import Planner, planner as _planner_default
from app.services.agent_run_repository import RunStateRepository, run_state_repository
from app.services.agent_run_state import (
    AgentPlan,
    AgentRunState,
    STATUS_RUNNING,
    RunStateMachine,
)
from app.services.llm_service import llm_service
from app.services.llm_observability_service import llm_observability_service
from app.services.prompt_service import prompt_service
from app.services.agent_json import extract_json_object as _extract_json_object
from app.services.agent_json import json_dumps as _json_dumps
from app.services.agent_json import json_loads_dict as _json_loads_dict
from app.services.conversation_memory_service import conversation_memory_service
from app.services.agent_harness_service import get_harness_profile
from app.services.agent_registry import AGENT_REGISTRY, AGENT_REGISTRY_VERSION, TASK_PROTOCOL_VERSION
from app.services.agent_skill_registry import resolve_agent_skill
from app.services.agent_audit import (
    EVENT_CANCEL,
    EVENT_COMPENSATION,
    EVENT_ERROR,
    EVENT_PLAN_CREATED,
    EVENT_RUN_STATE_CHANGED,
    agent_audit_service,
)
from app.workflows.langgraph_compat import GRAPH_END, GRAPH_START, StateGraph, workflow_engine_name


from app.services.agent_mixins import EvidenceVerificationMixin
from app.services.agent_prompts import (
    SUB_AGENTS,
    TOOL_DESCRIPTIONS,
    SUB_AGENT_DESCRIPTIONS,
    PRIORITY_FLOWS,
    EVIDENCE_SOURCE_TOOLS,
    EVIDENCE_GATED_WRITE_TOOLS,
    PARALLEL_READ_ONLY_TOOLS,
    PARALLEL_READ_ONLY_WORKER_PAIRS,
    PARALLEL_READ_ONLY_WORKERS,
    SUPERVISOR_ARTIFACT_TYPES,
    SUPERVISOR_RISK_LEVELS,
    POLICY_GUARDRAIL_ROLE,
    sanitize_agent_error_message as _sanitize_agent_error_message,
    normalize_decision as _normalize_decision,
    goal_execution_hints as _goal_execution_hints,
    build_demo_plan_preview as _build_demo_plan_preview,
    build_worker_system_prompt as _build_worker_system_prompt,
    build_preview_prompt as _build_preview_prompt,
)
from app.services.agent_workflow_nodes import AgentWorkflowNodesMixin


class AgentService(EvidenceVerificationMixin, AgentWorkflowNodesMixin):
    def __init__(self) -> None:
        self.settings = get_settings()
        # 职责拆分：Planner 只规划，PermissionGuard/ToolExecutor 管权限与执行，
        # RunStateRepository 管持久化，EvidenceVerifier 管证据校验。
        self._planner: Planner = _planner_default
        self._executor = tool_executor
        self._guard = permission_guard
        self._repo: RunStateRepository = run_state_repository
        self._workflow = self._build_workflow()

    @staticmethod
    def _is_cancel_requested(state: dict[str, Any]) -> bool:
        run_id = state["agent_run"].id
        status = state["db"].query(AgentRun.status).filter(AgentRun.id == run_id).scalar()
        return status == "cancelling"

    def _select_worker_agent(self, goal: str) -> str:
        return self._planner.plan_worker(goal)

    def _build_supervisor_worker_plan(self, goal: str) -> list[str]:
        """Return the ordered Worker handoff plan for cross-domain goals."""
        from app.services.agent_planner import build_worker_plan

        return build_worker_plan(goal)

    @staticmethod
    def _can_parallelize_workers(workers: list[str]) -> bool:
        return Planner._can_parallelize(workers)

    def _parallel_worker_plan(self, goal: str, workers: list[str]) -> dict[str, Any] | None:
        """Build bounded, explicit fan-out steps without asking the model to infer IDs."""
        return self._planner._parallel_plan(goal, workers)

    def _fallback_supervisor_plan(self, goal: str, *, reason: str | None = None) -> dict[str, Any]:
        return self._planner._fallback_dict(goal, reason=reason)

    def _validate_supervisor_plan(self, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        return self._planner._validate_dict(payload)

    async def _plan_with_supervisor(self, goal: str, user_id: int) -> dict[str, Any]:
        """兼容层：委托 Planner 生成 supervisor_plan dict（形状与历史一致）。"""
        return await self._planner.plan_dict(goal, user_id)

    def _worker_allows_tool(self, worker_name: str, tool_name: str) -> bool:
        """Delegate to the MCP permissions module."""
        return agent_allows_tool(worker_name, tool_name)

    @staticmethod
    def _build_task_contract(
        *,
        agent_run_id: int,
        goal: str,
        receiver: str,
        supervisor_plan: dict[str, Any],
        max_steps: int,
        sender: str = "supervisor_agent",
        parent_task_id: str | None = None,
        sequence: int = 0,
    ) -> dict[str, Any]:
        """Create the structured task envelope exchanged between roles.

        The envelope carries only execution constraints and references. Access
        control remains server-enforced and is never delegated to the model.
        """
        root_task_id = f"agent_run_{agent_run_id}"
        task_id = root_task_id if sequence == 0 else f"{root_task_id}.step_{sequence}"
        expected_artifacts = supervisor_plan.get("expected_artifacts")
        return {
            "protocol_version": TASK_PROTOCOL_VERSION,
            "task_id": task_id,
            "parent_task_id": parent_task_id,
            "sender": sender,
            "receiver": canonical_agent_type(receiver),
            "task_type": str(supervisor_plan.get("intent") or "legal_request"),
            "input": {"goal": goal},
            "constraints": {"language": "zh-CN", "max_steps": max_steps},
            "expected_output": {
                "artifacts": expected_artifacts if isinstance(expected_artifacts, list) else [],
                "format": "structured_result",
            },
            "skill": supervisor_plan.get("selected_skill") if isinstance(supervisor_plan.get("selected_skill"), dict) else None,
            "authorization": "server_enforced_rbac_acl_approval",
            "status": "assigned",
        }

    def _build_worker_messages(
        self,
        goal: str,
        worker_name: str,
        user_id: int,
        handoff_context: dict[str, Any] | None = None,
        memory_context: str = "",
        task_contract: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        worker_name = canonical_agent_type(worker_name)
        worker = SUB_AGENTS.get(worker_name) or SUB_AGENTS["knowledge_agent"]
        handoff_text = ""
        if handoff_context:
            handoff_text = (
                "\n\n上游 Worker 已完成，请仅使用以下结构化交接内容继续本职责：\n"
                f"{_json_dumps(handoff_context)}"
            )
        memory_text = f"\n\n用户会话记忆（仅作辅助上下文）：\n{memory_context}" if memory_context else ""
        task_text = (
            "\n\n结构化任务协议（权限由服务端强制执行，不得自行修改）：\n"
            f"{_json_dumps(task_contract)}"
            if task_contract
            else ""
        )
        return [
            {"role": "system", "content": _build_worker_system_prompt(worker_name, user_id)},
            {
                "role": "user",
                "content": (
                    "主 Agent 已完成任务分派。\n"
                    f"worker_agent: {worker_name}\n"
                    f"worker_scope: {worker['description']}\n"
                    f"goal: {goal}\n\n"
                    f"执行提示：\n{_goal_execution_hints(goal)}\n\n"
                    "请作为该从 Agent 逐步执行。需要工具时输出 tool_call；完成职责后输出 finish。"
                    f"{task_text}{handoff_text}{memory_text}"
                ),
            },
        ]

    def _build_handoff_context(self, logs: list[ToolCallLog], from_worker: str, answer: str) -> dict[str, Any]:
        completed_steps: list[dict[str, Any]] = []
        for log in logs[-6:]:
            if log.status != "success" or log.tool_name in {"finish", "evidence_verifier", "supervisor_handoff"}:
                continue
            observation = _json_loads_dict(log.observation)
            data = observation.get("data") if isinstance(observation.get("data"), dict) else {}
            completed_steps.append({"tool_name": log.tool_name, "data": data})
        return {
            "from_worker": from_worker,
            "completion_summary": answer,
            "completed_steps": completed_steps[-3:],
        }

    def _collect_run_artifacts(self, logs: list[ToolCallLog]) -> dict[str, list[dict[str, Any]]]:
        artifacts = {
            "documents": [],
            "tasks": [],
        }
        seen_keys: set[tuple[str, Any]] = set()

        def add_artifact(kind: str, key: Any, payload: dict[str, Any]) -> None:
            dedupe_key = (kind, key)
            if key is None or dedupe_key in seen_keys:
                return
            seen_keys.add(dedupe_key)
            artifacts[kind].append(payload)

        for log in logs:
            input_params = _json_loads_dict(log.input_params)
            observation = _json_loads_dict(log.observation)
            data = observation.get("data") if isinstance(observation.get("data"), dict) else {}

            document_id = data.get("document_id") or input_params.get("document_id")
            if log.tool_name in {"document_summary_tool", "document_risk_tool", "document_search_tool"} and document_id is not None:
                add_artifact(
                    "documents",
                    document_id,
                    {
                        "document_id": document_id,
                        "tool_name": log.tool_name,
                        "summary": data.get("summary"),
                        "risk_count": len(data.get("risks") or []) if isinstance(data.get("risks"), list) else 0,
                        "chunk_count": len(data.get("chunks") or []) if isinstance(data.get("chunks"), list) else 0,
                    },
                )
            if log.tool_name == "document_conflict_tool":
                for conflict_document_id in data.get("document_ids") or input_params.get("document_ids") or []:
                    add_artifact(
                        "documents",
                        conflict_document_id,
                        {
                            "document_id": conflict_document_id,
                            "tool_name": log.tool_name,
                            "conflict_count": len(data.get("conflicts") or []),
                        },
                    )

            tasks = data.get("tasks") if isinstance(data.get("tasks"), list) else []
            task_payload = data.get("task") if isinstance(data.get("task"), dict) else None
            if task_payload:
                tasks = [task_payload]
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                task_id = task.get("id")
                add_artifact(
                    "tasks",
                    task_id,
                    {
                        "task_id": task_id,
                        "title": task.get("title"),
                        "status": task.get("status"),
                        "priority": task.get("priority"),
                        "assignee": task.get("assignee"),
                        "tool_name": log.tool_name,
                    },
                )

        return artifacts

    def _record_run_summary(self, *, run: AgentRun, status: str, duration_ms: int, error_message: str | None = None) -> None:
        parsed_result = _json_loads_dict(run.result)
        llm_observability_service.log_event(
            module_name="agent",
            action="agent_run",
            model_name="agent_orchestrator",
            status=status,
            duration_ms=duration_ms,
            user_id=run.user_id,
            error_message=_sanitize_agent_error_message(error_message),
            request_excerpt={"run_id": run.id, "goal": run.goal},
            response_excerpt={
                "total_steps": run.total_steps,
                "final_status": run.status,
                "failure_reason": _sanitize_agent_error_message(run.failure_reason),
                "master_agent": parsed_result.get("master_agent"),
                "worker_agent": parsed_result.get("worker_agent"),
            },
        )

    async def _chat(self, messages: list[dict[str, str]], user_id: int) -> str:
        metadata = prompt_service.get_template_metadata("agent_system_prompt", user_id=user_id)
        try:
            return await llm_service.chat(
                messages,
                temperature=0.2,
                action="agent_plan",
                user_id=user_id,
                prompt_template=metadata.get("prompt_template"),
                prompt_version=metadata.get("prompt_version"),
            )
        except TypeError as exc:
            error_text = str(exc)
            if "unexpected keyword argument" not in error_text and "positional argument" not in error_text:
                raise
            return await llm_service.chat(messages, temperature=0.2)

    async def preview_plan(self, goal: str, user_id: int, max_steps: int = 5) -> dict[str, Any]:
        selected_skill = resolve_agent_skill(goal)
        demo_preview = _build_demo_plan_preview(goal, max_steps)
        if demo_preview:
            return {**demo_preview, "selected_skill": selected_skill}

        metadata = prompt_service.get_template_metadata("agent_plan_preview", user_id=user_id)
        prompt = _build_preview_prompt(goal, user_id=user_id)
        try:
            raw = await llm_service.generate(
                prompt,
                temperature=0.2,
                action="agent_plan_preview",
                user_id=user_id,
                prompt_template=metadata.get("prompt_template"),
                prompt_version=metadata.get("prompt_version"),
            )
        except TypeError as exc:
            error_text = str(exc)
            if "unexpected keyword argument" not in error_text and "positional argument" not in error_text:
                raise
            raw = await llm_service.generate(prompt, temperature=0.2)

        payload = llm_service.parse_json_object(raw)
        steps = payload.get("steps") if isinstance(payload.get("steps"), list) else []
        normalized_steps = []
        for index, step in enumerate(steps[:max_steps], start=1):
            if not isinstance(step, dict):
                continue
            tool_name = str(step.get("tool_name") or "").strip()
            if tool_name and tool_name not in {t["name"] for t in mcp_registry.list_all_tools()}:
                continue
            normalized_steps.append(
                {
                    "step": int(step.get("step") or index),
                    "tool_name": tool_name or "finish",
                    "purpose": str(step.get("purpose") or "").strip() or "待执行说明",
                    "action_input_preview": step.get("action_input_preview")
                    if isinstance(step.get("action_input_preview"), dict)
                    else {},
                }
            )

        risks = payload.get("risks") if isinstance(payload.get("risks"), list) else []
        normalized_risks = [str(item).strip() for item in risks if str(item).strip()]

        summary = str(payload.get("summary") or "").strip() or "将按目标分解步骤并调用相关工具执行。"
        can_execute = bool(payload.get("can_execute", True))
        estimated_steps = int(payload.get("estimated_steps") or len(normalized_steps) or 1)
        estimated_steps = max(1, min(estimated_steps, max_steps))

        if not normalized_steps:
            normalized_steps = [
                {
                    "step": 1,
                    "tool_name": "finish",
                    "purpose": "当前目标信息不足，建议补充更具体的对象编号或范围后再执行。",
                    "action_input_preview": {},
                }
            ]
            can_execute = False
            if not normalized_risks:
                normalized_risks.append("未能生成可靠的工具计划，请补充目标细节。")
            estimated_steps = 1

        return {
            "summary": summary,
            "estimated_steps": estimated_steps,
            "steps": normalized_steps,
            "risks": normalized_risks,
            "can_execute": can_execute,
            "selected_skill": selected_skill,
        }

    async def _emit_event(
        self,
        event_callback: Callable[[dict[str, Any]], Awaitable[None]] | None,
        payload: dict[str, Any],
    ) -> None:
        if not event_callback:
            return
        await event_callback(payload)

    def _create_run(
        self,
        goal: str,
        user_id: int,
        session_id: int | None,
        db: Session,
        *,
        trace_id: str | None = None,
        organization_id: int | None = None,
    ) -> AgentRun:
        agent_run = self._repo.create_run(
            db,
            goal=goal,
            user_id=user_id,
            session_id=session_id,
            trace_id=trace_id,
            organization_id=organization_id,
        )
        run_deadline = utc_now() + timedelta(seconds=self.settings.AGENT_RUN_DEADLINE_SECONDS)
        agent_run.run_deadline_at = run_deadline
        db.add(agent_run)
        db.commit()
        db.refresh(agent_run)
        return agent_run

    def _build_awaiting_approval_payload(
        self,
        *,
        agent_run_id: int,
        db: Session,
        user_id: int,
        master_agent: str,
        worker_agent: str,
        approval_request_id: int,
        tool_name: str,
        max_steps: int,
        worker_plan: list[str] | None = None,
        handoffs: list[dict[str, Any]] | None = None,
        supervisor_plan_details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        supervisor_plan = dict(supervisor_plan_details or {})
        supervisor_plan["workers"] = worker_plan or [worker_agent]
        supervisor_plan["handoffs"] = handoffs or []
        return {
            "final_answer": "执行已暂停，等待人工审批。",
            "master_agent": master_agent,
            "worker_agent": worker_agent,
            "agent_mode": "langgraph_workflow",
            "architecture_version": AGENT_REGISTRY_VERSION,
            "workflow_engine": workflow_engine_name(),
            "mcp_enabled": True,
            "policy_guardrails": ["rbac", "tool_acl", "approval", "evidence_verification"],
            "awaiting_approval": True,
            "approval_request_id": approval_request_id,
            "pending_tool_name": tool_name,
            "max_steps": max_steps,
            "supervisor_plan": supervisor_plan,
            "artifacts": self._collect_run_artifacts(
                self.get_run_logs(agent_run_id, db, user_id=user_id)
            ),
        }

    def _save_run(self, db: Session, agent_run: AgentRun, **fields) -> AgentRun:
        return self._repo.save_run(db, agent_run, **fields)

    @staticmethod
    def _load_workflow_snapshot(agent_run: AgentRun) -> dict[str, Any]:
        return run_state_repository.load_workflow_snapshot(agent_run)

    def _build_workflow_snapshot(self, state: dict[str, Any], *, node: str) -> dict[str, Any]:
        supervisor_plan = dict(state.get("supervisor_plan") or {})
        supervisor_plan["workers"] = list(state.get("worker_plan") or [state.get("worker_agent")])
        supervisor_plan["handoffs"] = list(state.get("handoffs") or [])
        return {
            "version": 3,
            "architecture_version": AGENT_REGISTRY_VERSION,
            "agent_registry_version": AGENT_REGISTRY_VERSION,
            "node": node,
            "task_contract": state.get("task_contract") or {},
            "worker_plan": list(state.get("worker_plan") or [state.get("worker_agent")]),
            "worker_agent": state.get("worker_agent"),
            "worker_index": int(state.get("worker_index") or 0),
            "handoffs": list(state.get("handoffs") or []),
            "parallel_plan": state.get("parallel_plan"),
            "parallel_results": state.get("parallel_results") or {},
            "step": int(state.get("step") or 0),
            "retry_count": int(state.get("retry_count") or 0),
            "evidence_scope_seen": bool(state.get("evidence_scope_seen")),
            "last_observation": state.get("last_observation") or "",
            "current_tool_name": state.get("current_tool_name"),
            "supervisor_plan": supervisor_plan,
            "updated_at": utc_now().isoformat(),
        }

    def _save_workflow_snapshot(self, state: dict[str, Any], *, node: str, **fields) -> AgentRun:
        model = state.get("_model")
        if not isinstance(model, AgentRunState):
            model = AgentRunState(
                run_id=state["agent_run"].id,
                user_id=state["user_id"],
                status=str(state["agent_run"].status or STATUS_RUNNING),
                node=node,
                step=int(state.get("step") or 0),
                retry_count=int(state.get("retry_count") or 0),
            )
        snapshot = self._build_workflow_snapshot(state, node=node)
        return self._repo.save_workflow_state(
            state["db"], state["agent_run"], snapshot=snapshot, state=model, node=node, **fields
        )

    def _append_observation(self, messages: list[dict[str, str]], raw: str, observation: str) -> None:
        messages.append({"role": "assistant", "content": raw})
        messages.append(
            {
                "role": "user",
                "content": (
                    "Observation:\n"
                    f"{observation}\n\n"
                    "请基于最新 observation 决定下一步，只输出 JSON。"
                ),
            }
        )

    def _create_log(
        self,
        db: Session,
        agent_run_id: int,
        step: int,
        decision: dict[str, Any],
        raw_decision: str,
        tool_name: str,
        input_params: dict[str, Any],
        observation: str,
        output_result: str,
        status: str,
        error: str | None,
        duration_ms: int,
    ) -> ToolCallLog:
        return self._repo.append_log(
            db,
            agent_run_id=agent_run_id,
            step=step,
            decision=decision,
            raw_decision=raw_decision,
            tool_name=tool_name,
            input_params=input_params,
            observation=observation,
            output_result=output_result,
            status=status,
            error=_sanitize_agent_error_message(error),
            duration_ms=duration_ms,
        )

    def _update_log(self, db: Session, log: ToolCallLog, **fields) -> ToolCallLog:
        return self._repo.update_log(db, log, **fields)

    async def _execute_tool(
        self,
        tool_name: str,
        action_input: dict[str, Any],
        user_id: int,
        db: Session,
        agent_type: str = "general_agent",
        agent_run_id: int | None = None,
        skip_approval: bool = False,
        *,
        step_id: int | None = None,
        trace_id: str | None = None,
        organization_id: int | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> tuple[dict, str | None]:
        """统一执行入口：委托 ToolExecutor（权限/取消/审批/幂等/超时/重试/审计）。

        Returns (result_dict, serialized_input_for_logging).
        """
        result, serialized_input = await self._executor.execute(
            tool_name,
            action_input,
            agent_type=agent_type,
            user_id=user_id,
            db=db,
            agent_run_id=agent_run_id,
            skip_approval=skip_approval,
            step_id=step_id,
            trace_id=trace_id,
            organization_id=organization_id,
            cancel_check=cancel_check,
        )
        return result, serialized_input

    def _assert_run_snapshot(self, db: Session, *, agent_run_id: int, user_id: int) -> dict | None:
        """校验该 Agent run 的权限快照；有效返回 None，失效返回拒绝结果。"""
        from app.services.authorization_service import authorization_service

        run = db.query(AgentRun).filter(AgentRun.id == agent_run_id).first()
        snapshot_id = run.authorization_snapshot_id if run else None
        if not snapshot_id:
            return None
        try:
            authorization_service.assert_snapshot(db, snapshot_id, user_id=user_id)
            return None
        except Exception as exc:
            code = getattr(getattr(exc, "detail", None), "get", lambda *_: "authz_changed")("code", "authz_changed")
            return {
                "success": False,
                "message": "执行已终止：权限已变化，请重新发起。",
                "data": {"error_code": code},
                "error": code,
                "mcp_error_code": "AUTHZ_CHANGED",
            }

    @staticmethod
    def _parallel_session_factory(db: Session):
        bind = db.get_bind()
        return sessionmaker(autocommit=False, autoflush=False, bind=bind)

    async def _execute_parallel_read_only_worker(
        self,
        *,
        worker_name: str,
        tool_name: str,
        action_input: dict[str, Any],
        user_id: int,
        db: Session,
        agent_run_id: int,
        step_id: int | None = None,
        trace_id: str | None = None,
        organization_id: int | None = None,
    ) -> dict[str, Any]:
        canonical_worker = canonical_agent_type(worker_name)
        if canonical_worker not in PARALLEL_READ_ONLY_WORKERS or tool_name not in PARALLEL_READ_ONLY_TOOLS:
            return {
                "worker_agent": worker_name,
                "tool_name": tool_name,
                "success": False,
                "error": "parallel_read_only_policy_denied",
                "data": {},
                "duration_ms": 0,
            }
        branch_db = self._parallel_session_factory(db)()
        started = time.time()
        try:
            result, serialized_input = await self._execute_tool(
                tool_name,
                action_input,
                user_id,
                branch_db,
                agent_type=canonical_worker,
                agent_run_id=agent_run_id,
                step_id=step_id,
                trace_id=trace_id,
                organization_id=organization_id,
            )
            return {
                "worker_agent": worker_name,
                "tool_name": tool_name,
                "action_input": json.loads(serialized_input or "{}"),
                "success": bool(result.get("success")),
                "error": result.get("error"),
                "data": result.get("data") if isinstance(result.get("data"), dict) else {},
                "duration_ms": int((time.time() - started) * 1000),
            }
        finally:
            branch_db.close()

    async def _run_parallel_read_only(self, state: dict[str, Any]) -> dict[str, Any]:
        branch_plan = state.get("parallel_plan") or {}
        semaphore = asyncio.Semaphore(self.settings.AGENT_PARALLEL_MAX_WORKERS)

        async def run_bounded(**kwargs):
            async with semaphore:
                return await self._execute_parallel_read_only_worker(**kwargs)

        jobs = []
        for index, (worker_name, step) in enumerate(branch_plan.items()):
            if not isinstance(step, dict):
                continue
            jobs.append(
                run_bounded(
                    worker_name=worker_name,
                    tool_name=str(step.get("tool_name") or ""),
                    action_input=step.get("action_input") if isinstance(step.get("action_input"), dict) else {},
                    user_id=state["user_id"],
                    db=state["db"],
                    agent_run_id=state["agent_run"].id,
                    step_id=int(state.get("step") or 0) + index + 1,
                    trace_id=state["agent_run"].trace_id,
                    organization_id=state["agent_run"].organization_id,
                )
            )
        results = await asyncio.gather(*jobs) if jobs else []
        return {item["worker_agent"]: item for item in results}

    def _build_run_result_payload(
        self,
        *,
        final_answer: str,
        agent_run_id: int,
        db: Session,
        user_id: int,
        master_agent: str,
        worker_agent: str,
        worker_plan: list[str] | None = None,
        handoffs: list[dict[str, Any]] | None = None,
        supervisor_plan_details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        logs = self.get_run_logs(agent_run_id, db, user_id=user_id)
        supervisor_plan = dict(supervisor_plan_details or {})
        supervisor_plan["workers"] = worker_plan or [worker_agent]
        supervisor_plan["handoffs"] = handoffs or []
        return {
            "final_answer": final_answer,
            "master_agent": master_agent,
            "worker_agent": worker_agent,
            "agent_mode": "langgraph_workflow",
            "architecture_version": AGENT_REGISTRY_VERSION,
            "workflow_engine": workflow_engine_name(),
            "mcp_enabled": True,
            "policy_guardrails": ["rbac", "tool_acl", "approval", "evidence_verification"],
            "artifacts": self._collect_run_artifacts(logs),
            "evidence_verification": self._latest_evidence_verification(logs),
            "supervisor_plan": supervisor_plan,
        }

    def _finalize_completed_run(
        self,
        *,
        db: Session,
        agent_run: AgentRun,
        final_answer: str,
        last_observation: str,
        failure_reason: str | None,
        total_steps: int,
        master_agent: str,
        worker_agent: str,
        run_started: float,
        summary_status: str,
        error_message: str | None = None,
        worker_plan: list[str] | None = None,
        handoffs: list[dict[str, Any]] | None = None,
        supervisor_plan_details: dict[str, Any] | None = None,
    ) -> AgentRun:
        result_run = self._save_run(
            db,
            agent_run,
            status="completed",
            result=_json_dumps(
                self._build_run_result_payload(
                    final_answer=final_answer,
                    agent_run_id=agent_run.id,
                    db=db,
                    user_id=agent_run.user_id,
                    master_agent=master_agent,
                    worker_agent=worker_agent,
                    worker_plan=worker_plan,
                    handoffs=handoffs,
                    supervisor_plan_details=supervisor_plan_details,
                )
            ),
            final_answer=final_answer,
            last_observation=last_observation,
            failure_reason=failure_reason,
            total_steps=total_steps,
            completed_at=utc_now(),
        )
        self._record_run_summary(
            run=result_run,
            status=summary_status,
            duration_ms=int((time.time() - run_started) * 1000),
            error_message=error_message,
        )
        return result_run

    def _build_workflow(self):
        graph = StateGraph(dict)
        graph.add_node("decide", self._workflow_decide)
        graph.add_node("parallel_fanout", self._workflow_parallel_fanout)
        graph.add_node("parallel_aggregate", self._workflow_parallel_aggregate)
        graph.add_node("cancelled", self._workflow_cancelled)
        graph.add_node("finish", self._workflow_finish)
        graph.add_node("retry", self._workflow_retry)
        graph.add_node("tool_call", self._workflow_tool_call)
        graph.add_node("verify_evidence", self._workflow_verify_evidence)
        graph.add_node("evidence_insufficient", self._workflow_evidence_insufficient)
        graph.add_node("partial", self._workflow_partial)
        graph.add_node("awaiting_approval", self._workflow_awaiting_approval)
        graph.add_edge(GRAPH_START, "decide")
        graph.add_conditional_edges(
            "decide",
            self._workflow_route_decision,
            {
                "finish": "finish",
                "retry": "retry",
                "tool_call": "tool_call",
                "verify_evidence": "verify_evidence",
                "parallel_fanout": "parallel_fanout",
                "cancelled": "cancelled",
            },
        )
        graph.add_conditional_edges(
            "verify_evidence",
            self._workflow_route_after_evidence_verification,
            {
                "tool_call": "tool_call",
                "finish": "finish",
                "evidence_insufficient": "evidence_insufficient",
                "parallel_aggregate": "parallel_aggregate",
            },
        )
        graph.add_conditional_edges(
            "parallel_fanout",
            self._workflow_route_after_parallel_fanout,
            {
                "verify_evidence": "verify_evidence",
                "parallel_aggregate": "parallel_aggregate",
            },
        )
        graph.add_conditional_edges(
            "finish",
            self._workflow_route_after_finish,
            {
                "handoff": "decide",
                "complete": GRAPH_END,
            },
        )
        graph.add_conditional_edges(
            "retry",
            self._workflow_route_continue,
            {
                "continue": "decide",
                "partial": "partial",
            },
        )
        graph.add_conditional_edges(
            "tool_call",
            self._workflow_route_continue,
            {
                "continue": "decide",
                "partial": "partial",
                "awaiting_approval": "awaiting_approval",
            },
        )
        graph.add_edge("partial", GRAPH_END)
        graph.add_edge("parallel_aggregate", GRAPH_END)
        graph.add_edge("cancelled", GRAPH_END)
        graph.add_edge("evidence_insufficient", GRAPH_END)
        graph.add_edge("awaiting_approval", GRAPH_END)
        return graph.compile()

    async def run(
        self,
        goal: str,
        user_id: int,
        db: Session,
        session_id: int | None = None,
        max_steps: int = 5,
        event_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> AgentRun:
        trace_id = new_trace_id()
        # 长流程权限快照：Agent 执行期间权限范围保持稳定，硬撤销立即终止。
        from app.services.authorization_service import authorization_service

        user_row = db.query(User).filter(User.id == user_id).first()
        organization_id = user_row.organization_id if user_row else None
        agent_run = self._create_run(
            goal=goal,
            user_id=user_id,
            session_id=session_id,
            db=db,
            trace_id=trace_id,
            organization_id=organization_id,
        )
        if user_row:
            try:
                ctx = authorization_service.build_context(db, user_row)
                snapshot_id = authorization_service.capture_snapshot(db, user_row, ctx)
                agent_run.authorization_snapshot_id = snapshot_id
                db.add(agent_run)
                db.commit()
            except Exception:
                db.rollback()
        memory_context = conversation_memory_service.build_agent_context(db, user_id, session_id)
        run_started = time.time()
        master_agent = "supervisor_agent"
        selected_skill = resolve_agent_skill(goal)
        harness_profile = get_harness_profile()
        supervisor_plan = await self._plan_with_supervisor(goal, user_id)
        supervisor_plan["harness"] = harness_profile
        supervisor_plan["selected_skill"] = selected_skill
        worker_plan = supervisor_plan["workers"]
        worker_agent = worker_plan[0]
        task_contract = self._build_task_contract(
            agent_run_id=agent_run.id,
            goal=goal,
            receiver=worker_agent,
            supervisor_plan=supervisor_plan,
            max_steps=max_steps,
        )
        supervisor_plan = {
            **supervisor_plan,
            "agent_registry_version": AGENT_REGISTRY_VERSION,
            "harness": harness_profile,
            "selected_skill": selected_skill,
            "task_contract": task_contract,
            "active_task_contract": task_contract,
        }
        await self._emit_event(
            event_callback,
            {
                "type": "run_started",
                "run_id": agent_run.id,
                "goal": goal,
                "status": agent_run.status,
                "master_agent": master_agent,
                "worker_agent": worker_agent,
                "supervisor_plan": supervisor_plan,
                "task_contract": task_contract,
                "created_at": agent_run.created_at.isoformat() if agent_run.created_at else None,
            },
        )
        state = {
            "goal": goal,
            "user_id": user_id,
            "db": db,
            "session_id": session_id,
            "memory_context": memory_context,
            "max_steps": max_steps,
            "event_callback": event_callback,
            "agent_run": agent_run,
            "run_started": run_started,
            "master_agent": master_agent,
            "worker_agent": worker_agent,
            "supervisor_plan": supervisor_plan,
            "task_contract": task_contract,
            "worker_plan": worker_plan,
            "worker_index": 0,
            "handoffs": [],
            "handoff_pending": False,
            "parallel_plan": supervisor_plan.get("parallel_plan"),
            "parallel_pending": bool(supervisor_plan.get("parallel_plan")),
            "parallel_results": {},
            "messages": self._build_worker_messages(
                goal,
                worker_agent,
                user_id,
                memory_context=memory_context,
                task_contract=task_contract,
            ),
            "last_observation": "",
            "step": 0,
            "final_run": None,
            "evidence_scope_seen": False,
            "retry_count": 0,
        }
        # 具类型运行状态（取代无约束 dict 的持久化/恢复载体），并写计划审计。
        state["_model"] = AgentRunState(
            run_id=agent_run.id,
            user_id=user_id,
            status=STATUS_RUNNING,
            node="decide",
            step=0,
            trace_id=trace_id,
            organization_id=organization_id,
            plan=AgentPlan.from_dict(supervisor_plan),
            run_deadline_at=agent_run.run_deadline_at.isoformat() if agent_run.run_deadline_at else None,
        )
        try:
            agent_audit_service.record(
                db=db, event_type=EVENT_PLAN_CREATED, run_id=agent_run.id, trace_id=trace_id,
                user_id=user_id, organization_id=organization_id,
                decision={"plan_source": supervisor_plan.get("plan_source"), "workers": worker_plan},
                status="created",
            )
        except Exception:  # noqa: BLE001 - 审计失败不阻断执行
            db.rollback()
        self._save_workflow_snapshot(state, node="decide")

        try:
            final_state = await self._workflow.ainvoke(state)
            return final_state.get("final_run") or agent_run
        except Exception as exc:
            safe_error = _sanitize_agent_error_message(str(exc))
            result_run = self._save_run(
                db,
                agent_run,
                status="error",
                error=safe_error,
                failure_reason=safe_error,
                last_observation=state.get("last_observation") or "",
                completed_at=utc_now(),
            )
            # 失败后补偿：反向补偿已完成的可补偿写步骤（不可补偿步骤记录审计）。
            try:
                from app.services.agent_compensation import run_compensation

                run_compensation(db, result_run)
            except Exception:  # noqa: BLE001 - 补偿失败不阻断错误上报
                db.rollback()
            try:
                agent_audit_service.record(
                    db=db, event_type=EVENT_ERROR, run_id=result_run.id,
                    trace_id=result_run.trace_id, user_id=result_run.user_id,
                    organization_id=result_run.organization_id, status="error",
                    summary={"error_category": "unhandled_exception"},
                )
            except Exception:  # noqa: BLE001
                db.rollback()
            self._record_run_summary(
                run=result_run,
                status="error",
                duration_ms=int((time.time() - run_started) * 1000),
                error_message=safe_error,
            )
            await self._emit_event(
                event_callback,
                {
                    "type": "run_failed",
                    "run": self.serialize_run(result_run),
                    "master_agent": master_agent,
                    "worker_agent": worker_agent,
                },
            )
            if getattr(exc, "status_code", None) is not None:
                raise
            return result_run

    def _rebuild_messages_from_logs(
        self,
        *,
        goal: str,
        worker_agent: str,
        user_id: int,
        db: Session,
        session_id: int | None,
        logs: list[ToolCallLog],
        task_contract: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        memory_context = conversation_memory_service.build_agent_context(db, user_id, session_id)
        messages = self._build_worker_messages(
            goal,
            worker_agent,
            user_id,
            memory_context=memory_context,
            task_contract=task_contract,
        )
        for log in logs:
            if log.status == "pending_approval":
                continue
            if not log.raw_decision or not log.observation:
                continue
            if log.tool_name == "finish":
                continue
            self._append_observation(messages, log.raw_decision, log.observation)
        return messages

    async def resume_after_approval(
        self,
        approval_id: int,
        user_id: int,
        db: Session,
        event_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> AgentRun:
        approval = agent_approval_service.get_request(db=db, approval_id=approval_id, user_id=user_id)
        if not approval:
            raise ValueError("Approval request not found")
        if approval.status not in {"approved", "executed"}:
            raise ValueError("Approval request is not approved")
        if approval.status == "executed":
            run = self.get_run(approval.agent_run_id or 0, db, user_id=user_id)
            if not run:
                raise ValueError("Agent run not found")
            return run
        if not approval.agent_run_id:
            raise ValueError("Approval request is not bound to a run")

        agent_run = self.get_run(approval.agent_run_id, db, user_id=user_id)
        if not agent_run:
            raise ValueError("Agent run not found")

        run_payload = _json_loads_dict(agent_run.result)
        snapshot = self._load_workflow_snapshot(agent_run)
        master_agent = str(run_payload.get("master_agent") or "supervisor_agent")
        supervisor_plan = snapshot.get("supervisor_plan") if isinstance(snapshot.get("supervisor_plan"), dict) else {}
        if not supervisor_plan:
            supervisor_plan = run_payload.get("supervisor_plan") if isinstance(run_payload.get("supervisor_plan"), dict) else {}
        worker_plan = supervisor_plan.get("workers") if isinstance(supervisor_plan.get("workers"), list) else []
        if not worker_plan:
            worker_plan = snapshot.get("worker_plan") if isinstance(snapshot.get("worker_plan"), list) else []
        if not worker_plan:
            worker_plan = self._build_supervisor_worker_plan(agent_run.goal)
        worker_plan = [canonical_agent_type(str(worker)) for worker in worker_plan]
        supervisor_plan["workers"] = worker_plan
        handoffs = snapshot.get("handoffs") if isinstance(snapshot.get("handoffs"), list) else []
        if not handoffs:
            handoffs = supervisor_plan.get("handoffs") if isinstance(supervisor_plan.get("handoffs"), list) else []
        worker_agent = canonical_agent_type(str(snapshot.get("worker_agent") or run_payload.get("worker_agent") or worker_plan[-1]))
        task_contract = snapshot.get("task_contract") if isinstance(snapshot.get("task_contract"), dict) else {}
        if not task_contract:
            candidate = supervisor_plan.get("active_task_contract") or supervisor_plan.get("task_contract")
            task_contract = candidate if isinstance(candidate, dict) else {}
        if not task_contract:
            task_contract = self._build_task_contract(
                agent_run_id=agent_run.id,
                goal=agent_run.goal,
                receiver=worker_agent,
                supervisor_plan=supervisor_plan,
                max_steps=max(int(agent_run.total_steps or 0) + 1, 5),
            )
        worker_index = int(snapshot.get("worker_index")) if isinstance(snapshot.get("worker_index"), int) else -1
        if worker_index < 0 or worker_index >= len(worker_plan):
            worker_index = worker_plan.index(worker_agent) if worker_agent in worker_plan else len(worker_plan) - 1
        max_steps = int(run_payload.get("max_steps") or max(int(agent_run.total_steps or 0) + 1, 5))
        logs = self.get_run_logs(agent_run.id, db, user_id=user_id)
        pending_log = next((item for item in reversed(logs) if item.status == "pending_approval"), None)
        if not pending_log:
            raise ValueError("Pending approval step not found")

        await self._emit_event(
            event_callback,
            {
                "type": "run_resumed",
                "run_id": agent_run.id,
                "approval_request_id": approval.id,
                "tool_name": pending_log.tool_name,
            },
        )

        run_started = time.time()
        self._save_run(
            db,
            agent_run,
            status="running",
            final_answer=None,
            completed_at=None,
        )
        decision = _normalize_decision(pending_log.raw_decision or "")
        action_input = _json_loads_dict(pending_log.input_params)
        action_input.pop("_master_agent", None)
        action_input.pop("_worker_agent", None)

        execution_agent = canonical_agent_type(approval.agent_type or worker_agent)
        # 审批参数守卫：审批后参数变化/审批过期 → 必须重新审批，不执行工具。
        from app.services.agent_approval_service import ApprovalStateError

        try:
            agent_approval_service.require_executable(
                db=db, approval_id=approval.id, user_id=user_id, current_params=action_input
            )
        except ApprovalStateError as exc:
            agent_approval_service.create_request(
                db=db,
                user_id=user_id,
                tool_name=pending_log.tool_name,
                input_params=action_input,
                agent_type=execution_agent,
                agent_run_id=agent_run.id,
                step_id=pending_log.step,
            )
            self._save_run(db, agent_run, status="awaiting_approval", final_answer="审批参数已变化，需重新审批。")
            raise ValueError(f"Approval parameters changed; re-approval required: {exc}")

        result, serialized_input = await self._execute_tool(
            pending_log.tool_name,
            action_input,
            user_id,
            db,
            agent_type=execution_agent,
            agent_run_id=agent_run.id,
            skip_approval=True,
            step_id=pending_log.step,
            trace_id=agent_run.trace_id,
            organization_id=agent_run.organization_id,
            cancel_check=lambda: self._is_cancel_requested({"db": db, "agent_run": agent_run}),
        )
        result.setdefault("data", {})
        if isinstance(result["data"], dict):
            result["data"].setdefault("master_agent", master_agent)
            result["data"].setdefault("worker_agent", execution_agent)
        observation = _json_dumps(result)
        duration_ms = 0
        status = "success" if result.get("success") else "error"
        error = result.get("error")

        self._update_log(
            db,
            pending_log,
            status="approved",
            error=None,
            output_result="approval_granted",
        )
        logged_input = json.loads(serialized_input) if serialized_input else action_input
        logged_input["_master_agent"] = master_agent
        logged_input["_worker_agent"] = execution_agent
        new_log = self._create_log(
            db=db,
            agent_run_id=agent_run.id,
            step=pending_log.step or int(agent_run.total_steps or 0),
            decision=decision,
            raw_decision=pending_log.raw_decision or "",
            tool_name=pending_log.tool_name,
            input_params=logged_input,
            observation=observation,
            output_result=observation,
            status=status,
            error=error,
            duration_ms=duration_ms,
        )
        await self._emit_event(
            event_callback,
            {
                "type": "step_completed",
                "run_id": agent_run.id,
                "log": self.serialize_log(new_log),
                "master_agent": master_agent,
                "worker_agent": execution_agent,
            },
        )
        agent_approval_service.mark_executed(
            db=db,
            approval_id=approval.id,
            user_id=user_id,
            decision_note=approval.decision_note or "审批通过后已恢复执行",
        )
        messages = self._rebuild_messages_from_logs(
            goal=agent_run.goal,
            worker_agent=worker_agent,
            user_id=user_id,
            db=db,
            session_id=agent_run.session_id,
            logs=self.get_run_logs(agent_run.id, db, user_id=user_id),
            task_contract=task_contract,
        )
        memory_context = conversation_memory_service.build_agent_context(db, user_id, agent_run.session_id)
        state = {
            "goal": agent_run.goal,
            "user_id": user_id,
            "db": db,
            "session_id": agent_run.session_id,
            "memory_context": memory_context,
            "max_steps": max_steps,
            "event_callback": event_callback,
            "agent_run": agent_run,
            "run_started": run_started,
            "master_agent": master_agent,
            "worker_agent": worker_agent,
            "supervisor_plan": supervisor_plan,
            "task_contract": task_contract,
            "messages": messages,
            "last_observation": observation,
            "step": pending_log.step or int(agent_run.total_steps or 0),
            "final_run": None,
            "evidence_scope_seen": self._has_evidence_source_logs(logs),
            "worker_plan": worker_plan,
            "worker_index": worker_index,
            "handoffs": handoffs,
            "handoff_pending": False,
            "parallel_plan": snapshot.get("parallel_plan") or supervisor_plan.get("parallel_plan"),
            "parallel_pending": False,
            "parallel_results": snapshot.get("parallel_results") or {},
            "retry_count": int(snapshot.get("retry_count") or 0),
        }
        state["_model"] = AgentRunState(
            run_id=agent_run.id,
            user_id=user_id,
            status=STATUS_RUNNING,
            node="decide",
            step=state["step"],
            trace_id=agent_run.trace_id,
            organization_id=agent_run.organization_id,
            plan=AgentPlan.from_dict(supervisor_plan),
            run_deadline_at=agent_run.run_deadline_at.isoformat() if agent_run.run_deadline_at else None,
            retry_count=state["retry_count"],
        )
        self._save_workflow_snapshot(
            state,
            node="decide",
            last_observation=observation,
            failure_reason=_sanitize_agent_error_message(error),
            total_steps=state["step"],
        )
        if state["step"] >= max_steps:
            state["awaiting_approval"] = False
            partial_state = await self._workflow_partial(state)
            return partial_state.get("final_run") or agent_run
        final_state = await self._workflow.ainvoke(state)
        return final_state.get("final_run") or agent_run

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
    ) -> list[AgentRun]:
        type_mapping = {
            "document": ("documents", "document_id"),
            "task": ("tasks", "task_id"),
        }
        artifact_key = type_mapping.get((artifact_type or "").strip().lower())
        if not artifact_key:
            return []

        bucket_name, id_field = artifact_key
        runs = (
            db.query(AgentRun)
            .filter(AgentRun.user_id == user_id)
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
            .all()
        )

        matched: list[AgentRun] = []
        for run in runs:
            artifacts = self.serialize_run(run).get("artifacts") or {}
            rows = artifacts.get(bucket_name) if isinstance(artifacts, dict) else []
            if not isinstance(rows, list):
                continue
            if any(isinstance(row, dict) and int(row.get(id_field) or 0) == artifact_id for row in rows):
                matched.append(run)
        return matched

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


agent_service = AgentService()
