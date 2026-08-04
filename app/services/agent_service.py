import asyncio
import json
import re
import time
from datetime import timedelta
from typing import Any, Awaitable, Callable

from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.time import utc_now
from app.services.agent_approval_service import agent_approval_service
from app.mcp.permissions import (
    CANONICAL_AGENT_TYPES,
    agent_allows_tool,
    allowed_tools_for,
    canonical_agent_type,
)
from app.mcp.registry import mcp_registry
from app.models.agent import AgentRun, ToolCallLog
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
from app.workflows.langgraph_compat import GRAPH_END, GRAPH_START, StateGraph, workflow_engine_name


# ── Canonical domain-agent definitions ─────────────────────────────────
# These are kept here for prompt-building (label, description) and goal
# routing.  The actual tool-permission matrix lives in
# app.mcp.permissions.AGENT_TOOL_ALLOW — always consult that module for
# enforcement.

SUB_AGENTS = {
    agent_type: {**config, "tools": tuple(sorted(allowed_tools_for(agent_type)))}
    for agent_type, config in AGENT_REGISTRY.items()
}


def _build_tool_descriptions(tool_names: tuple[str, ...] | list[str] | None = None) -> str:
    """Build tool description text from the MCP registry.

    When ``tool_names`` is given, only descriptions for those tools are
    included — this is how sub-agent prompts get their scoped tool list.
    """
    all_tools = {t["name"]: t for t in mcp_registry.list_all_tools()}
    selected_names = list(tool_names) if tool_names else list(all_tools.keys())
    descriptions = []
    for index, tool_name in enumerate(selected_names):
        spec = all_tools.get(tool_name)
        if not spec:
            continue
        descriptions.append(
            (
                f"{index + 1}. {spec['name']}\n"
                f"   description: {spec['description']}\n"
                f"   parameters: {json.dumps(spec.get('input_schema', {}), ensure_ascii=False)}\n"
            )
        )
    return "\n".join(descriptions)


def _build_sub_agent_descriptions() -> str:
    lines = []
    for name in CANONICAL_AGENT_TYPES:
        config = SUB_AGENTS[name]
        tool_list = ", ".join(sorted(config["tools"]))
        lines.append(
            f"- {name}（{config['label']}）：{config['description']} "
            f"输入：{config['input_contract']}；输出：{config['output_contract']}；"
            f"禁止：{config['forbidden']}；可用工具：{tool_list}"
        )
    return "\n".join(lines)


TOOL_DESCRIPTIONS = _build_tool_descriptions()
SUB_AGENT_DESCRIPTIONS = _build_sub_agent_descriptions()

PRIORITY_FLOWS = (
    "- 总结会议并创建任务：meeting_summary_tool -> meeting_action_tool -> finish\n"
    "- 查询未完成任务并生成邮件：task_query_tool(status=todo 或 in_progress) -> email_writer_tool -> finish\n"
    "- 总结文档并提取风险：document_summary_tool -> document_risk_tool -> finish"
)

EVIDENCE_SOURCE_TOOLS = {
    "document_search_tool",
    "document_summary_tool",
    "document_risk_tool",
    "document_conflict_tool",
    "meeting_summary_tool",
    "meeting_query_tool",
}

EVIDENCE_GATED_WRITE_TOOLS = {
    "task_create_tool",
    "meeting_action_tool",
}

# Only these tools may run in the concurrent fan-out. The list intentionally
# excludes all side effects and tools that can open an approval workflow.
PARALLEL_READ_ONLY_TOOLS = {
    "document_search_tool",
    "document_summary_tool",
    "document_risk_tool",
    "document_conflict_tool",
    "meeting_query_tool",
}
PARALLEL_READ_ONLY_WORKER_PAIRS = {
    frozenset({"knowledge_agent", "meeting_agent"}),
    frozenset({"legal_compliance_agent", "meeting_agent"}),
}
PARALLEL_READ_ONLY_WORKERS = {"knowledge_agent", "meeting_agent", "legal_compliance_agent"}

SUPERVISOR_ARTIFACT_TYPES = {"document", "meeting", "task", "email"}
SUPERVISOR_RISK_LEVELS = {"low", "medium", "high"}
POLICY_GUARDRAIL_ROLE = "policy_guardrail"


def _sanitize_agent_error_message(error: str | None) -> str | None:
    if not error:
        return None
    if error in {"Invalid JSON response", "Agent 决策要求重试"}:
        return error
    if error.startswith("Invalid action_type:"):
        return error
    return "Agent 执行失败，请查看系统日志"


def _normalize_decision(raw: str) -> dict[str, Any]:
    payload = _extract_json_object(raw)
    if not payload:
        return {
            "thought": "模型输出不是合法 JSON，需要重试。",
            "action_type": "retry",
            "tool_name": "",
            "action_input": {},
            "answer": "",
            "parse_error": "Invalid JSON response",
        }

    action_type = str(payload.get("action_type") or "").strip()
    tool_name = str(payload.get("tool_name") or "").strip()
    action_input = payload.get("action_input") if isinstance(payload.get("action_input"), dict) else {}
    answer = str(payload.get("answer") or "").strip()
    thought = str(payload.get("thought") or "").strip()

    legacy_action = str(payload.get("action") or "").strip()
    if not action_type and legacy_action:
        if legacy_action == "finish":
            action_type = "finish"
            answer = answer or str(action_input.get("answer") or "").strip()
        else:
            action_type = "tool_call"
            tool_name = legacy_action

    if action_type not in {"tool_call", "finish", "retry"}:
        return {
            "thought": thought or "模型返回了非法 action_type，需要重试。",
            "action_type": "retry",
            "tool_name": tool_name,
            "action_input": action_input,
            "answer": answer,
            "parse_error": f"Invalid action_type: {action_type or '<empty>'}",
        }

    return {
        "thought": thought,
        "action_type": action_type,
        "tool_name": tool_name,
        "action_input": action_input,
        "answer": answer,
        "parse_error": payload.get("parse_error"),
    }


def _goal_execution_hints(goal: str) -> str:
    normalized = goal.lower()
    hints: list[str] = []

    if ("会议" in goal or "meeting" in normalized) and ("任务" in goal or "待办" in goal or "action item" in normalized):
        hints.append("建议优先使用 meeting_summary_tool，然后使用 meeting_action_tool，最后 finish。")

    if ("任务" in goal or "task" in normalized) and ("邮件" in goal or "email" in normalized):
        hints.append("建议先用 task_query_tool 查询 todo 或 in_progress 任务，再用 email_writer_tool 生成邮件。")

    if ("文档" in goal or "document" in normalized) and ("风险" in goal or "risk" in normalized):
        hints.append("建议先用 document_summary_tool，再用 document_risk_tool，最后 finish。")

    if ("冲突" in goal or "核对" in goal or "对比" in goal or "conflict" in normalized):
        hints.append("涉及多份文档的日期、金额或负责人冲突时，使用 document_conflict_tool，并依据返回的原文定位汇总结论。")

    if not hints:
        hints.append("请优先选择最少但有效的工具步骤，完成后及时 finish。")

    return "\n".join(hints)


def _build_demo_plan_preview(goal: str, max_steps: int) -> dict[str, Any] | None:
    match = re.search(r"总结文档\s*(\d+)", goal)
    has_risk_intent = "风险" in goal or "risk" in goal.lower()
    if not match or not has_risk_intent:
        return None

    document_id = int(match.group(1))
    steps = [
        {
            "step": 1,
            "tool_name": "document_summary_tool",
            "purpose": "先获取文档摘要，明确文档主题、范围和关键背景。",
            "action_input_preview": {"document_id": document_id},
        },
        {
            "step": 2,
            "tool_name": "document_risk_tool",
            "purpose": "基于同一文档提取风险点、风险说明和建议动作。",
            "action_input_preview": {"document_id": document_id},
        },
        {
            "step": 3,
            "tool_name": "finish",
            "purpose": "汇总摘要和风险结论，形成最终答复。",
            "action_input_preview": {},
        },
    ]
    return {
        "summary": "标准演示链路会先总结文档，再提取风险点，最后汇总成可展示的执行结果。",
        "estimated_steps": min(3, max_steps),
        "steps": steps[:max_steps],
        "risks": [
            "如果文档不存在、无权限或尚未完成索引，文档类工具会直接失败。",
            "如果文档内容过于简短或缺少明确条款，风险提取结果可能较少。",
        ],
        "can_execute": max_steps >= 3,
    }


def _build_worker_system_prompt(worker_name: str, user_id: int | None = None) -> str:
    worker_name = canonical_agent_type(worker_name)
    worker = SUB_AGENTS.get(worker_name) or SUB_AGENTS["knowledge_agent"]
    scoped_descriptions = _build_tool_descriptions(worker["tools"])
    prompt = prompt_service.render_by_name(
        "agent_system_prompt",
        user_id=user_id,
        tool_descriptions=scoped_descriptions,
        priority_flows=PRIORITY_FLOWS,
        sub_agent_descriptions=SUB_AGENT_DESCRIPTIONS,
    )
    return (
        f"你当前作为 {worker_name}（{worker['label']}）执行任务。\n"
        f"职责边界：{worker['description']}\n"
        f"输入契约：{worker.get('input_contract', '用户目标和授权上下文')}\n"
        f"输出契约：{worker.get('output_contract', '结构化执行结果')}\n"
        f"禁止事项：{worker.get('forbidden', '不得绕过权限、审批和参数校验')}\n"
        "只能选择本从 Agent 工具清单中列出的工具；如目标已经完成，请 finish。\n\n"
        f"{prompt}"
    )


def _build_preview_prompt(goal: str, user_id: int | None = None) -> str:
    return prompt_service.render_by_name(
        "agent_plan_preview",
        user_id=user_id,
        tool_descriptions=TOOL_DESCRIPTIONS,
        priority_flows=PRIORITY_FLOWS,
        sub_agent_descriptions=SUB_AGENT_DESCRIPTIONS,
        goal=goal,
        execution_hints=_goal_execution_hints(goal),
    )


class AgentService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._workflow = self._build_workflow()

    @staticmethod
    def _is_cancel_requested(state: dict[str, Any]) -> bool:
        run_id = state["agent_run"].id
        status = state["db"].query(AgentRun.status).filter(AgentRun.id == run_id).scalar()
        return status == "cancelling"

    def _select_worker_agent(self, goal: str) -> str:
        normalized = (goal or "").lower()
        has_document = "文档" in goal or "合同" in goal or "方案" in goal or "document" in normalized or "risk" in normalized or "冲突" in goal or "核对" in goal or "对比" in goal or "conflict" in normalized
        has_legal = "合同" in goal or "条款" in goal or "合规" in goal or "法务" in goal or "违约" in goal or "审查" in goal
        has_project = "项目" in goal or "里程碑" in goal or "延期" in goal or "排期" in goal or "依赖" in goal
        has_meeting = "会议" in goal or "纪要" in goal or "meeting" in normalized or "action item" in normalized
        has_task = "任务" in goal or "待办" in goal or "task" in normalized or "todo" in normalized
        has_email = "邮件" in goal or "催办" in goal or "email" in normalized or "mail" in normalized
        has_sql = "sql" in normalized or "数据库" in goal or "查询表" in goal
        has_sales_report = "销售日报" in goal or "sales daily" in normalized

        if has_sql or has_sales_report:
            return "data_agent"
        if has_legal:
            return "legal_compliance_agent"
        if has_project:
            return "project_agent"
        if has_document:
            return "knowledge_agent"
        if has_meeting:
            return "meeting_agent"
        if has_email:
            return "communication_agent"
        if has_task:
            return "workflow_agent"
        # Ambiguous requests go to the read-only knowledge role, which must
        # clarify or refuse when it has no evidence instead of taking action.
        return "knowledge_agent"

    def _build_supervisor_worker_plan(self, goal: str) -> list[str]:
        """Return the ordered Worker handoff plan for cross-domain goals."""
        normalized = (goal or "").lower()
        has_document = "文档" in goal or "合同" in goal or "方案" in goal or "document" in normalized or "risk" in normalized or "冲突" in goal or "核对" in goal or "对比" in goal or "conflict" in normalized
        has_legal = "合同" in goal or "条款" in goal or "合规" in goal or "法务" in goal or "违约" in goal or "审查" in goal
        has_project = "项目" in goal or "里程碑" in goal or "延期" in goal or "排期" in goal or "依赖" in goal
        has_meeting = "会议" in goal or "纪要" in goal or "meeting" in normalized
        has_task = "任务" in goal or "待办" in goal or "task" in normalized or "todo" in normalized
        has_email = "邮件" in goal or "催办" in goal or "email" in normalized or "mail" in normalized
        has_sales_report = "销售日报" in goal or "sales daily" in normalized

        plan: list[str] = []
        if has_legal:
            plan.append("legal_compliance_agent")
        if has_project:
            plan.append("project_agent")
        if has_document and not has_legal and not has_project:
            plan.append("knowledge_agent")
        if has_meeting:
            plan.append("meeting_agent")
        if has_sales_report:
            plan.append("data_agent")
        elif "sql" in normalized or "数据库" in goal or "查询表" in goal:
            plan.append("data_agent")
        if has_task:
            plan.append("workflow_agent")
        if has_email:
            plan.append("communication_agent")
        return plan or [self._select_worker_agent(goal)]

    @staticmethod
    def _can_parallelize_workers(workers: list[str]) -> bool:
        canonical_workers = {canonical_agent_type(worker) for worker in workers}
        return len(workers) == 2 and frozenset(canonical_workers) in PARALLEL_READ_ONLY_WORKER_PAIRS

    def _parallel_worker_plan(self, goal: str, workers: list[str]) -> dict[str, Any] | None:
        """Build bounded, explicit fan-out steps without asking the model to infer IDs."""
        if not self._can_parallelize_workers(workers):
            return None
        document_match = re.search(r"(?:文档|合同|方案|document)\s*(?:id)?\s*(\d+)", goal, flags=re.IGNORECASE)
        meeting_match = re.search(r"(?:会议|纪要|meeting)\s*(?:id)?\s*(\d+)", goal, flags=re.IGNORECASE)
        if not document_match or not meeting_match:
            return None
        document_id = int(document_match.group(1))
        meeting_id = int(meeting_match.group(1))
        document_tool = "document_risk_tool" if ("风险" in goal or "risk" in goal.lower()) else "document_summary_tool"
        document_worker = next(
            (worker for worker in workers if canonical_agent_type(worker) in {"knowledge_agent", "legal_compliance_agent"}),
            "knowledge_agent",
        )
        meeting_worker = next(
            (worker for worker in workers if canonical_agent_type(worker) == "meeting_agent"),
            "meeting_agent",
        )
        return {
            document_worker: {"tool_name": document_tool, "action_input": {"document_id": document_id}},
            meeting_worker: {"tool_name": "meeting_query_tool", "action_input": {"meeting_id": meeting_id}},
        }

    def _fallback_supervisor_plan(self, goal: str, *, reason: str | None = None) -> dict[str, Any]:
        workers = self._build_supervisor_worker_plan(goal)
        normalized = (goal or "").lower()
        expected_artifacts: list[str] = []
        if "knowledge_agent" in workers:
            expected_artifacts.append("document")
        if "meeting_agent" in workers:
            expected_artifacts.append("meeting")
        if "任务" in goal or "待办" in goal or "task" in normalized or "todo" in normalized:
            expected_artifacts.append("task")
        if "邮件" in goal or "催办" in goal or "email" in normalized or "mail" in normalized:
            expected_artifacts.append("email")
        if "data_agent" in workers and ("日报" in goal or "报告" in goal or "report" in normalized):
            expected_artifacts.append("document")
        expected_artifacts = list(dict.fromkeys(expected_artifacts))
        parallel_plan = self._parallel_worker_plan(goal, workers)
        return {
            "intent": (goal or "").strip() or "general_legal_request",
            "workers": workers,
            "dependencies": [
                {"from": workers[index], "to": workers[index + 1]}
                for index in range(len(workers) - 1)
            ],
            "risk_level": "medium" if any(worker in {"workflow_agent", "data_agent", "communication_agent"} for worker in workers) else "low",
            "expected_artifacts": expected_artifacts,
            "rationale": "使用规则路由生成稳定的最小 Worker 计划。",
            "plan_source": "rule_fallback",
            "fallback_reason": reason,
            "execution_mode": "parallel_read_only" if parallel_plan else "sequential",
            "parallel_plan": parallel_plan,
            "architecture_version": AGENT_REGISTRY_VERSION,
            "guardrail_nodes": ["rbac", "tool_acl", "approval", "evidence_verification"],
        }

    def _validate_supervisor_plan(self, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        allowed_workers = set(CANONICAL_AGENT_TYPES) | {"document_agent", "task_agent", "task_email_agent"}
        workers = payload.get("workers")
        if not isinstance(workers, list) or not workers or len(workers) > 4:
            return None, "workers 必须是 1 到 4 个 Worker 的列表"
        requested_workers = [str(item).strip() for item in workers]
        if any(item not in allowed_workers for item in requested_workers):
            return None, "计划包含未知或内部 Worker"
        normalized_workers = [canonical_agent_type(item) for item in requested_workers]
        if len(set(normalized_workers)) != len(normalized_workers):
            return None, "Worker 不允许重复"

        dependencies = payload.get("dependencies")
        if dependencies is None:
            dependencies = []
        if not isinstance(dependencies, list):
            return None, "dependencies 必须是列表"
        normalized_dependencies = []
        for dependency in dependencies:
            if not isinstance(dependency, dict):
                return None, "dependency 必须是对象"
            source = canonical_agent_type(str(dependency.get("from") or "").strip())
            target = canonical_agent_type(str(dependency.get("to") or "").strip())
            if source not in normalized_workers or target not in normalized_workers:
                return None, "dependency 指向计划外 Worker"
            if normalized_workers.index(source) >= normalized_workers.index(target):
                return None, "dependency 必须从前序 Worker 指向后序 Worker"
            normalized_dependencies.append({"from": source, "to": target})

        risk_level = str(payload.get("risk_level") or "medium").strip().lower()
        if risk_level not in SUPERVISOR_RISK_LEVELS:
            return None, "risk_level 非法"
        artifacts = payload.get("expected_artifacts")
        if artifacts is None:
            artifacts = []
        if not isinstance(artifacts, list):
            return None, "expected_artifacts 必须是列表"
        normalized_artifacts = [str(item).strip().lower() for item in artifacts if str(item).strip()]
        if any(item not in SUPERVISOR_ARTIFACT_TYPES for item in normalized_artifacts):
            return None, "expected_artifacts 包含非法类型"

        return {
            "intent": str(payload.get("intent") or "general_legal_request").strip() or "general_legal_request",
            "workers": normalized_workers,
            "dependencies": normalized_dependencies,
            "risk_level": risk_level,
            "expected_artifacts": list(dict.fromkeys(normalized_artifacts)),
            "rationale": str(payload.get("rationale") or "Supervisor 已完成 Worker 分派。").strip(),
            "plan_source": "llm",
            "fallback_reason": None,
            "execution_mode": "sequential",
            "parallel_plan": None,
            "architecture_version": AGENT_REGISTRY_VERSION,
            "guardrail_nodes": ["rbac", "tool_acl", "approval", "evidence_verification"],
        }, None

    async def _plan_with_supervisor(self, goal: str, user_id: int) -> dict[str, Any]:
        required_workers = self._build_supervisor_worker_plan(goal)
        if len(required_workers) == 1:
            plan = self._fallback_supervisor_plan(goal)
            plan.update(
                {
                    "plan_source": "deterministic_direct_route",
                    "fallback_reason": None,
                    "rationale": "单领域请求直接路由到唯一责任 Agent，不启动多 Agent 规划。",
                }
            )
            return plan

        metadata = prompt_service.get_template_metadata("agent_supervisor_plan", user_id=user_id)
        prompt = prompt_service.render_by_name(
            "agent_supervisor_plan",
            user_id=user_id,
            sub_agent_descriptions=SUB_AGENT_DESCRIPTIONS,
            goal=goal,
        )
        try:
            raw = await llm_service.generate(
                prompt,
                temperature=0.1,
                action="agent_supervisor_plan",
                user_id=user_id,
                prompt_template=metadata.get("prompt_template"),
                prompt_version=metadata.get("prompt_version"),
            )
        except Exception:
            return self._fallback_supervisor_plan(goal, reason="supervisor_generation_failed")

        payload = llm_service.parse_json_object(raw)
        plan, error = self._validate_supervisor_plan(payload)
        if plan:
            if plan["workers"] != required_workers:
                return self._fallback_supervisor_plan(goal, reason="supervisor_role_boundary_mismatch")
            parallel_plan = self._parallel_worker_plan(goal, plan["workers"])
            if parallel_plan:
                plan["execution_mode"] = "parallel_read_only"
                plan["parallel_plan"] = parallel_plan
            return plan
        return self._fallback_supervisor_plan(goal, reason=error or "supervisor_plan_invalid")

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
            "meetings": [],
            "tasks": [],
            "emails": [],
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

            meeting_id = data.get("meeting_id") or input_params.get("meeting_id")
            if log.tool_name in {"meeting_summary_tool", "meeting_query_tool", "meeting_action_tool"} and meeting_id is not None:
                add_artifact(
                    "meetings",
                    meeting_id,
                    {
                        "meeting_id": meeting_id,
                        "tool_name": log.tool_name,
                        "theme": data.get("theme"),
                        "action_item_count": len(data.get("action_items") or []) if isinstance(data.get("action_items"), list) else 0,
                        "task_count": len(data.get("tasks") or []) if isinstance(data.get("tasks"), list) else 0,
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

            draft_id = data.get("draft_id")
            if log.tool_name == "email_writer_tool" and draft_id is not None:
                add_artifact(
                    "emails",
                    draft_id,
                    {
                        "draft_id": draft_id,
                        "subject": data.get("subject"),
                        "recipient": data.get("recipient"),
                        "purpose": data.get("purpose"),
                        "tool_name": log.tool_name,
                    },
                )

        return artifacts

    @staticmethod
    def _has_evidence_source_logs(logs: list[ToolCallLog]) -> bool:
        return any(log.tool_name in EVIDENCE_SOURCE_TOOLS and log.status == "success" for log in logs)

    def _verify_evidence(self, logs: list[ToolCallLog]) -> dict[str, Any]:
        """Validate that structured claims retain a source before a write or final answer.

        The verifier is intentionally deterministic. It does not generate new business
        conclusions, so an unsupported model response cannot approve its own evidence.
        """
        checks: list[dict[str, Any]] = []

        def verify_claims(tool_name: str, claim_type: str, claims: Any) -> None:
            if not isinstance(claims, list):
                return
            for index, claim in enumerate(claims, start=1):
                if not isinstance(claim, dict):
                    checks.append({"tool_name": tool_name, "claim_type": claim_type, "index": index, "passed": False})
                    continue
                has_evidence = bool(str(claim.get("evidence") or claim.get("source_text") or "").strip())
                checks.append(
                    {
                        "tool_name": tool_name,
                        "claim_type": claim_type,
                        "index": index,
                        "passed": has_evidence,
                    }
                )

        for log in logs:
            if log.status != "success" or log.tool_name not in EVIDENCE_SOURCE_TOOLS:
                continue
            observation = _json_loads_dict(log.observation)
            data = observation.get("data") if isinstance(observation.get("data"), dict) else {}
            if log.tool_name == "document_search_tool":
                chunks = data.get("chunks")
                if isinstance(chunks, list):
                    for index, chunk in enumerate(chunks, start=1):
                        metadata = chunk.get("metadata") if isinstance(chunk, dict) else {}
                        has_locator = bool(
                            isinstance(chunk, dict)
                            and str(chunk.get("content") or "").strip()
                            and isinstance(metadata, dict)
                            and (metadata.get("page_number") is not None or metadata.get("section_title") or metadata.get("chunk_id"))
                        )
                        checks.append(
                            {
                                "tool_name": log.tool_name,
                                "claim_type": "retrieval_chunk",
                                "index": index,
                                "passed": has_locator,
                            }
                        )
            elif log.tool_name == "document_risk_tool":
                verify_claims(log.tool_name, "risk", data.get("risks"))
            elif log.tool_name == "document_conflict_tool":
                conflicts = data.get("conflicts")
                if isinstance(conflicts, list):
                    for index, conflict in enumerate(conflicts, start=1):
                        sources = [
                            conflict.get("source_a") if isinstance(conflict, dict) else None,
                            conflict.get("source_b") if isinstance(conflict, dict) else None,
                        ]
                        for source_index, source in enumerate(sources, start=1):
                            has_locator = isinstance(source, dict) and bool(
                                str(source.get("source_text") or "").strip()
                                and (
                                    source.get("chunk_id") is not None
                                    or source.get("page_number") is not None
                                    or source.get("section_title")
                                )
                            )
                            checks.append(
                                {
                                    "tool_name": log.tool_name,
                                    "claim_type": "cross_document_conflict",
                                    "index": index,
                                    "source_index": source_index,
                                    "passed": has_locator,
                                }
                            )
            else:
                verify_claims(log.tool_name, "decision", data.get("decisions"))
                verify_claims(log.tool_name, "action_item", data.get("action_items"))
                verify_claims(log.tool_name, "risk", data.get("risks"))

        failed = [item for item in checks if not item["passed"]]
        return {
            "applicable": bool(checks),
            "passed": not failed,
            "checked_claims": len(checks),
            "failed_claims": len(failed),
            "issues": failed[:20],
        }

    def _latest_evidence_verification(self, logs: list[ToolCallLog]) -> dict[str, Any] | None:
        for log in reversed(logs):
            if log.tool_name != "evidence_verifier" or not log.observation:
                continue
            observation = _json_loads_dict(log.observation)
            data = observation.get("data")
            if isinstance(data, dict):
                return data
        return None

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

    def _create_run(self, goal: str, user_id: int, session_id: int | None, db: Session) -> AgentRun:
        agent_run = AgentRun(
            user_id=user_id,
            session_id=session_id,
            goal=goal,
            status="running",
            total_steps=0,
        )
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
        for key, value in fields.items():
            setattr(agent_run, key, value)
        db.add(agent_run)
        db.commit()
        db.refresh(agent_run)
        return agent_run

    @staticmethod
    def _load_workflow_snapshot(agent_run: AgentRun) -> dict[str, Any]:
        return _json_loads_dict(agent_run.workflow_state)

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
        fields.update(
            {
                "workflow_state": _json_dumps(self._build_workflow_snapshot(state, node=node)),
                "workflow_state_updated_at": utc_now(),
            }
        )
        return self._save_run(state["db"], state["agent_run"], **fields)

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
        safe_input = {key: value for key, value in input_params.items() if key != "db"}
        log = ToolCallLog(
            agent_run_id=agent_run_id,
            step=step,
            action_type=decision.get("action_type"),
            thought=decision.get("thought"),
            tool_name=tool_name,
            input_params=_json_dumps(safe_input),
            raw_decision=raw_decision,
            observation=observation,
            output_result=output_result,
            status=status,
            error=_sanitize_agent_error_message(error),
            duration_ms=duration_ms,
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        return log

    def _update_log(self, db: Session, log: ToolCallLog, **fields) -> ToolCallLog:
        for key, value in fields.items():
            setattr(log, key, value)
        db.add(log)
        db.commit()
        db.refresh(log)
        return log

    async def _execute_tool(
        self,
        tool_name: str,
        action_input: dict[str, Any],
        user_id: int,
        db: Session,
        agent_type: str = "general_agent",
        agent_run_id: int | None = None,
        skip_approval: bool = False,
    ) -> tuple[dict, str | None]:
        """Execute a tool through the MCP registry.

        The registry handles:
          1. Tool lookup
          2. Permission check (agent_type × tool_name)
          3. JSON Schema argument validation
          4. Auto-context injection (user_id, db)
          5. Invocation
          6. Result normalisation
          7. Observability hooks

        Returns (result_dict, serialized_input_for_logging).
        """
        try:
            result = await asyncio.wait_for(
                mcp_registry.call_tool(
                    tool_name, action_input, agent_type=agent_type, user_id=user_id, db=db,
                    agent_run_id=agent_run_id, skip_approval=skip_approval,
                ),
                timeout=self.settings.AGENT_TOOL_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            result = {
                "success": False,
                "message": "工具执行超时，已停止等待该步骤。",
                "data": {"tool_name": tool_name, "timeout_seconds": self.settings.AGENT_TOOL_TIMEOUT_SECONDS},
                "error": "agent_tool_timeout",
                "mcp_error_code": "AGENT_TOOL_TIMEOUT",
            }
        serialized_input = _json_dumps({k: v for k, v in action_input.items() if k != "db"})
        return result, serialized_input

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
        for worker_name, step in branch_plan.items():
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

    def _workflow_route_decision(self, state: dict[str, Any]) -> str:
        if state.get("needs_evidence_verification"):
            return "verify_evidence"
        return str((state.get("current_decision") or {}).get("action_type") or "retry")

    def _workflow_route_after_parallel_fanout(self, state: dict[str, Any]) -> str:
        return "verify_evidence" if state.get("needs_evidence_verification") else "parallel_aggregate"

    def _workflow_route_after_evidence_verification(self, state: dict[str, Any]) -> str:
        if not (state.get("evidence_verification") or {}).get("passed", True):
            return "evidence_insufficient"
        return str(state.get("verification_target") or "finish")

    def _workflow_route_after_finish(self, state: dict[str, Any]) -> str:
        return "handoff" if state.get("handoff_pending") else "complete"

    def _workflow_route_continue(self, state: dict[str, Any]) -> str:
        if state.get("awaiting_approval"):
            return "awaiting_approval"
        return "partial" if int(state.get("step") or 0) >= int(state.get("max_steps") or 0) else "continue"

    async def _workflow_decide(self, state: dict[str, Any]) -> dict[str, Any]:
        if self._is_cancel_requested(state):
            state.update({"current_decision": {"action_type": "cancelled"}, "current_raw": "cancel_requested"})
            return state
        if state.get("parallel_pending"):
            state.update(
                {
                    "parallel_pending": False,
                    "current_decision": {"action_type": "parallel_fanout", "thought": "[supervisor_agent] 启动只读 Worker 并行分支。"},
                    "current_tool_name": "parallel_fanout",
                    "current_raw": "parallel_read_only",
                }
            )
            self._save_workflow_snapshot(state, node="parallel_fanout")
            return state
        step = int(state.get("step") or 0) + 1
        started = time.time()
        raw = await self._chat(state["messages"], state["user_id"])
        decision = _normalize_decision(raw)
        action_type = decision["action_type"]
        tool_name = decision["tool_name"] or action_type
        safe_input = decision["action_input"] if isinstance(decision["action_input"], dict) else {}
        step_worker_agent = canonical_agent_type(state["worker_agent"])
        if action_type == "tool_call" and not self._worker_allows_tool(step_worker_agent, tool_name):
            requested_tool = tool_name
            worker_plan = [canonical_agent_type(worker) for worker in (state.get("worker_plan") or [])]
            current_index = int(state.get("worker_index") or 0)
            next_authorized_worker = next(
                (
                    worker
                    for worker in worker_plan[current_index + 1 :]
                    if self._worker_allows_tool(worker, requested_tool)
                ),
                None,
            )
            if next_authorized_worker:
                # A downstream Worker owns the requested capability. Close the
                # current role and use the normal Supervisor handoff path so
                # the next role receives only structured upstream context.
                action_type = "finish"
                tool_name = "finish"
                safe_input = {}
                decision.update(
                    {
                        "action_type": "finish",
                        "tool_name": "",
                        "answer": f"当前职责已完成，交由 {next_authorized_worker} 继续处理。",
                        "parse_error": None,
                        "thought": (
                            f"当前 {step_worker_agent} 无权调用 {requested_tool}；"
                            f"Supervisor 将按已批准计划交接给 {next_authorized_worker}。"
                        ),
                    }
                )
            else:
                action_type = "retry"
                tool_name = "retry"
                decision.update(
                    {
                        "action_type": "retry",
                        "tool_name": "",
                        "parse_error": f"role_boundary_violation:{step_worker_agent}:{requested_tool}",
                        "thought": (
                            f"当前 {step_worker_agent} 无权调用 {requested_tool}；"
                            "跨角色操作必须由 Supervisor 显式交接。"
                        ),
                    }
                )
        has_evidence_context = bool(state.get("evidence_scope_seen"))
        requires_verification = (
            (action_type == "finish" and has_evidence_context)
            or (action_type == "tool_call" and tool_name in EVIDENCE_GATED_WRITE_TOOLS and has_evidence_context)
        )
        thought = decision.get("thought")
        decision["thought"] = (
            f"[{state['master_agent']} -> {step_worker_agent}] {thought}"
            if thought
            else f"[{state['master_agent']} -> {step_worker_agent}]"
        )
        await self._emit_event(
            state.get("event_callback"),
            {
                "type": "step_started",
                "run_id": state["agent_run"].id,
                "step": step,
                "action_type": action_type,
                "tool_name": tool_name,
                "thought": decision.get("thought"),
                "input_params": safe_input,
                "master_agent": state["master_agent"],
                "worker_agent": step_worker_agent,
            },
        )
        state.update(
            {
                "step": step,
                "step_started_at": started,
                "current_raw": raw,
                "current_decision": decision,
                "current_action_type": action_type,
                "current_tool_name": tool_name,
                "current_safe_input": safe_input,
                "current_worker_agent": step_worker_agent,
                "needs_evidence_verification": requires_verification,
                "verification_target": action_type,
            }
        )
        return state

    async def _workflow_cancelled(self, state: dict[str, Any]) -> dict[str, Any]:
        answer = "执行已取消，后续步骤未继续运行。"
        log = self._create_log(
            db=state["db"], agent_run_id=state["agent_run"].id, step=int(state.get("step") or 0),
            decision={"action_type": "cancelled", "thought": "[supervisor_agent] 检测到取消请求。"}, raw_decision=state.get("current_raw") or "cancel_requested",
            tool_name="run_cancelled", input_params={}, observation=_json_dumps({"success": False, "message": answer}),
            output_result=answer, status="cancelled", error="cancelled_by_user", duration_ms=0,
        )
        run = self._save_run(
            state["db"], state["agent_run"], status="cancelled", final_answer=answer, failure_reason="cancelled_by_user",
            total_steps=int(state.get("step") or 0), completed_at=utc_now(),
        )
        await self._emit_event(state.get("event_callback"), {"type": "run_completed", "run": self.serialize_run(run), "master_agent": state["master_agent"], "worker_agent": state.get("worker_agent")})
        state["final_run"] = run
        return state

    async def _workflow_parallel_fanout(self, state: dict[str, Any]) -> dict[str, Any]:
        started = time.time()
        branches = await self._run_parallel_read_only(state)
        branch_logs = []
        for index, branch in enumerate(branches.values(), start=1):
            observation = _json_dumps(
                {
                    "success": branch["success"],
                    "message": "并行只读分支完成" if branch["success"] else "并行只读分支失败",
                    "data": branch["data"],
                    "error": branch.get("error"),
                }
            )
            log = self._create_log(
                db=state["db"],
                agent_run_id=state["agent_run"].id,
                step=int(state.get("step") or 0) + index,
                decision={"action_type": "parallel_tool_call", "thought": f"[supervisor_agent -> {branch['worker_agent']}] 并行只读分支"},
                raw_decision="parallel_read_only",
                tool_name=branch["tool_name"],
                input_params={**branch.get("action_input", {}), "_worker_agent": branch["worker_agent"], "_parallel_branch": True},
                observation=observation,
                output_result=observation,
                status="success" if branch["success"] else "error",
                error=branch.get("error"),
                duration_ms=branch["duration_ms"],
            )
            branch_logs.append(self.serialize_log(log))
            await self._emit_event(
                state.get("event_callback"),
                {"type": "step_completed", "run_id": state["agent_run"].id, "log": self.serialize_log(log), "master_agent": state["master_agent"], "worker_agent": branch["worker_agent"]},
            )
        fanout_observation = _json_dumps(
            {"success": all(item["success"] for item in branches.values()), "data": {"branches": branches, "execution_mode": "parallel_read_only"}}
        )
        fanout_log = self._create_log(
            db=state["db"], agent_run_id=state["agent_run"].id, step=int(state.get("step") or 0) + len(branches) + 1,
            decision={"action_type": "parallel_fanout", "thought": "[supervisor_agent] 并行只读 Worker 已启动。"}, raw_decision="parallel_read_only",
            tool_name="supervisor_parallel_fanout", input_params={"workers": list(branches)}, observation=fanout_observation,
            output_result=fanout_observation, status="success" if all(item["success"] for item in branches.values()) else "error",
            error=None, duration_ms=int((time.time() - started) * 1000),
        )
        state.update(
            {
                "parallel_results": branches,
                "parallel_branch_logs": branch_logs,
                "step": int(state.get("step") or 0) + len(branches) + 1,
                "last_observation": fanout_observation,
                "evidence_scope_seen": any(item["success"] and item["tool_name"] in EVIDENCE_SOURCE_TOOLS for item in branches.values()),
                "needs_evidence_verification": any(item["success"] and item["tool_name"] in EVIDENCE_SOURCE_TOOLS for item in branches.values()),
                "verification_target": "parallel_aggregate",
            }
        )
        self._save_workflow_snapshot(state, node="parallel_fanout")
        return state

    async def _workflow_parallel_aggregate(self, state: dict[str, Any]) -> dict[str, Any]:
        branches = state.get("parallel_results") or {}
        completed = [item for item in branches.values() if item.get("success")]
        failed = [item for item in branches.values() if not item.get("success")]
        findings: list[dict[str, Any]] = []
        for branch in completed:
            data = branch.get("data") or {}
            for claim_type in ("risks", "decisions", "action_items"):
                for item in data.get(claim_type) or []:
                    if not isinstance(item, dict):
                        continue
                    findings.append({
                        "worker_agent": branch["worker_agent"],
                        "type": claim_type,
                        "title": item.get("title") or item.get("task") or "待确认事项",
                        "evidence": item.get("evidence") or item.get("source_text"),
                        "severity": item.get("severity"),
                    })
        conclusion = f"已并行完成 {len(completed)} 个只读 Worker 的结果汇聚"
        answer_lines = [conclusion]
        if findings:
            answer_lines.append("关键发现：" + "；".join(str(item["title"]) for item in findings[:5]))
        answer_lines.append("建议动作：核对每项原文证据后，再通过确认流程创建风险任务。")
        answer = "\n".join(answer_lines)
        if failed:
            answer += f"\n其中 {len(failed)} 个分支失败，请查看执行日志。"
        else:
            answer += "\n结果均已保留来源与执行记录。"
        aggregation = {
            "execution_mode": "parallel_read_only",
            "conclusion": conclusion,
            "findings": findings[:12],
            "recommended_actions": ["核对原文证据后确认最终业务口径。", "需要推进时通过确认流程创建风险任务。"],
            "completed_workers": [item["worker_agent"] for item in completed],
            "failed_workers": [item["worker_agent"] for item in failed],
            "branches": branches,
        }
        state["supervisor_plan"] = {**state.get("supervisor_plan", {}), "aggregation": aggregation}
        observation = _json_dumps({"success": not failed, "data": aggregation})
        log = self._create_log(
            db=state["db"], agent_run_id=state["agent_run"].id, step=int(state.get("step") or 0) + 1,
            decision={"action_type": "aggregate", "thought": "[supervisor_agent] 汇聚并行只读 Worker 输出。"}, raw_decision="parallel_read_only",
            tool_name="supervisor_aggregate", input_params={"workers": list(branches)}, observation=observation,
            output_result=answer, status="success" if not failed else "partial", error=None, duration_ms=0,
        )
        await self._emit_event(state.get("event_callback"), {"type": "step_completed", "run_id": state["agent_run"].id, "log": self.serialize_log(log), "master_agent": state["master_agent"], "worker_agent": "supervisor_agent"})
        state["last_observation"] = observation
        self._save_workflow_snapshot(state, node="parallel_aggregate")
        result_run = self._finalize_completed_run(
            db=state["db"], agent_run=state["agent_run"], final_answer=answer, last_observation=observation,
            failure_reason=None if not failed else "parallel_branch_failed", total_steps=int(state.get("step") or 0) + 1,
            master_agent=state["master_agent"], worker_agent="supervisor_agent", run_started=state["run_started"],
            summary_status="success" if not failed else "partial", error_message=None if not failed else "parallel_branch_failed",
            worker_plan=state.get("worker_plan"), handoffs=state.get("handoffs"), supervisor_plan_details=state.get("supervisor_plan"),
        )
        await self._emit_event(state.get("event_callback"), {"type": "run_completed", "run": self.serialize_run(result_run), "master_agent": state["master_agent"], "worker_agent": "supervisor_agent"})
        state["final_run"] = result_run
        return state

    async def _workflow_verify_evidence(self, state: dict[str, Any]) -> dict[str, Any]:
        logs = self.get_run_logs(state["agent_run"].id, state["db"], user_id=state["user_id"])
        verification = self._verify_evidence(logs)
        observation = _json_dumps(
            {
                "success": verification["passed"],
                "message": "证据核验通过" if verification["passed"] else "证据核验未通过",
                "data": verification,
            }
        )
        verifier_decision = {
            "action_type": "verify",
            "thought": f"[{state['master_agent']} -> {POLICY_GUARDRAIL_ROLE}] 核验结构化结论的原文依据。",
        }
        log = self._create_log(
            db=state["db"],
            agent_run_id=state["agent_run"].id,
            step=state["step"],
            decision=verifier_decision,
            raw_decision=state["current_raw"],
            tool_name="evidence_verifier",
            input_params={"verification_target": state.get("verification_target")},
            observation=observation,
            output_result=observation,
            status="success" if verification["passed"] else "error",
            error=None if verification["passed"] else "Evidence verification failed",
            duration_ms=0,
        )
        await self._emit_event(
            state.get("event_callback"),
            {
                "type": "step_completed",
                "run_id": state["agent_run"].id,
                "log": self.serialize_log(log),
                "master_agent": state["master_agent"],
                "worker_agent": POLICY_GUARDRAIL_ROLE,
            },
        )
        state["evidence_verification"] = verification
        self._save_workflow_snapshot(state, node="verify_evidence")
        return state

    async def _workflow_evidence_insufficient(self, state: dict[str, Any]) -> dict[str, Any]:
        verification = state.get("evidence_verification") or {}
        failed_claims = int(verification.get("failed_claims") or 0)
        answer = f"任务未继续执行：发现 {failed_claims} 条结论缺少原文证据，请补充资料或重新分析。"
        self._save_workflow_snapshot(state, node="evidence_insufficient")
        result_run = self._finalize_completed_run(
            db=state["db"],
            agent_run=state["agent_run"],
            final_answer=answer,
            last_observation=state["last_observation"],
            failure_reason="evidence_verification_failed",
            total_steps=state["step"],
            master_agent=state["master_agent"],
            worker_agent=state["worker_agent"],
            run_started=state["run_started"],
            summary_status="partial",
            error_message="evidence_verification_failed",
            worker_plan=state.get("worker_plan"),
            handoffs=state.get("handoffs"),
            supervisor_plan_details=state.get("supervisor_plan"),
        )
        await self._emit_event(
            state.get("event_callback"),
            {
                "type": "run_completed",
                "run": self.serialize_run(result_run),
                "master_agent": state["master_agent"],
                "worker_agent": POLICY_GUARDRAIL_ROLE,
            },
        )
        state["final_run"] = result_run
        return state

    async def _workflow_finish(self, state: dict[str, Any]) -> dict[str, Any]:
        decision = state["current_decision"]
        answer = decision["answer"] or "任务已完成。"
        duration_ms = int((time.time() - state["step_started_at"]) * 1000)
        log = self._create_log(
            db=state["db"],
            agent_run_id=state["agent_run"].id,
            step=state["step"],
            decision=decision,
            raw_decision=state["current_raw"],
            tool_name="finish",
            input_params={},
            observation=state["last_observation"],
            output_result=answer,
            status="success",
            error=None,
            duration_ms=duration_ms,
        )
        await self._emit_event(
            state.get("event_callback"),
            {
                "type": "step_completed",
                "run_id": state["agent_run"].id,
                "log": self.serialize_log(log),
            },
        )
        next_worker_index = int(state.get("worker_index") or 0) + 1
        worker_plan = state.get("worker_plan") or [state["worker_agent"]]
        if next_worker_index < len(worker_plan):
            next_worker = worker_plan[next_worker_index]
            handoff_context = self._build_handoff_context(
                self.get_run_logs(state["agent_run"].id, state["db"], user_id=state["user_id"]),
                state["worker_agent"],
                answer,
            )
            next_task_contract = self._build_task_contract(
                agent_run_id=state["agent_run"].id,
                goal=state["goal"],
                receiver=next_worker,
                supervisor_plan=state["supervisor_plan"],
                max_steps=state["max_steps"],
                sender=state["worker_agent"],
                parent_task_id=str((state.get("task_contract") or {}).get("task_id") or "") or None,
                sequence=next_worker_index,
            )
            handoff = {
                "from_worker": state["worker_agent"],
                "to_worker": next_worker,
                "completion_summary": answer,
                "step": state["step"],
                "task_contract": next_task_contract,
            }
            handoffs = list(state.get("handoffs") or [])
            handoffs.append(handoff)
            handoff_log = self._create_log(
                db=state["db"],
                agent_run_id=state["agent_run"].id,
                step=state["step"],
                decision={"action_type": "handoff", "thought": f"[{state['master_agent']}] Worker 交接"},
                raw_decision=state["current_raw"],
                tool_name="supervisor_handoff",
                input_params={"from_worker": state["worker_agent"], "to_worker": next_worker},
                observation=_json_dumps({"success": True, "data": handoff_context}),
                output_result="worker_handoff",
                status="success",
                error=None,
                duration_ms=0,
            )
            await self._emit_event(
                state.get("event_callback"),
                {
                    "type": "worker_handoff",
                    "run_id": state["agent_run"].id,
                    "log": self.serialize_log(handoff_log),
                    "from_worker": state["worker_agent"],
                    "to_worker": next_worker,
                },
            )
            state["supervisor_plan"] = {
                **state["supervisor_plan"],
                "active_task_contract": next_task_contract,
            }
            state.update(
                {
                    "worker_agent": next_worker,
                    "worker_index": next_worker_index,
                    "messages": self._build_worker_messages(
                        state["goal"],
                        next_worker,
                        state["user_id"],
                        handoff_context=handoff_context,
                        memory_context=state.get("memory_context") or "",
                        task_contract=next_task_contract,
                    ),
                    "task_contract": next_task_contract,
                    "handoffs": handoffs,
                    "handoff_pending": True,
                    "needs_evidence_verification": False,
                }
            )
            self._save_workflow_snapshot(state, node="decide")
            return state
        self._save_workflow_snapshot(state, node="completed")
        result_run = self._finalize_completed_run(
            db=state["db"],
            agent_run=state["agent_run"],
            final_answer=answer,
            last_observation=state["last_observation"],
            failure_reason=None,
            total_steps=state["step"],
            master_agent=state["master_agent"],
            worker_agent=state["worker_agent"],
            run_started=state["run_started"],
            summary_status="success",
            worker_plan=worker_plan,
            handoffs=state.get("handoffs"),
            supervisor_plan_details=state.get("supervisor_plan"),
        )
        await self._emit_event(
            state.get("event_callback"),
            {
                "type": "run_completed",
                "run": self.serialize_run(result_run),
                "master_agent": state["master_agent"],
                "worker_agent": state["worker_agent"],
            },
        )
        state["final_run"] = result_run
        state["handoff_pending"] = False
        return state

    async def _workflow_retry(self, state: dict[str, Any]) -> dict[str, Any]:
        decision = state["current_decision"]
        error_message = decision.get("parse_error") or "Agent 决策要求重试"
        observation = _json_dumps(
            {
                "success": False,
                "message": "请修正输出后继续",
                "data": {
                    "master_agent": state["master_agent"],
                    "worker_agent": state["current_worker_agent"],
                },
                "error": error_message,
            }
        )
        duration_ms = int((time.time() - state["step_started_at"]) * 1000)
        log = self._create_log(
            db=state["db"],
            agent_run_id=state["agent_run"].id,
            step=state["step"],
            decision=decision,
            raw_decision=state["current_raw"],
            tool_name="retry",
            input_params={
                **state["current_safe_input"],
                "_master_agent": state["master_agent"],
                "_worker_agent": state["current_worker_agent"],
            },
            observation=observation,
            output_result=observation,
            status="error",
            error=error_message,
            duration_ms=duration_ms,
        )
        await self._emit_event(
            state.get("event_callback"),
            {
                "type": "step_completed",
                "run_id": state["agent_run"].id,
                "log": self.serialize_log(log),
                "master_agent": state["master_agent"],
                "worker_agent": state["current_worker_agent"],
            },
        )
        state["last_observation"] = observation
        state["retry_count"] = int(state.get("retry_count") or 0) + 1
        self._save_workflow_snapshot(
            state,
            node="decide",
            last_observation=observation,
            failure_reason=_sanitize_agent_error_message(error_message),
            total_steps=state["step"],
        )
        self._append_observation(state["messages"], state["current_raw"], observation)
        return state

    async def _workflow_tool_call(self, state: dict[str, Any]) -> dict[str, Any]:
        result, serialized_input = await self._execute_tool(
            state["current_tool_name"],
            state["current_safe_input"],
            state["user_id"],
            state["db"],
            agent_type=state["current_worker_agent"],
            agent_run_id=state["agent_run"].id,
        )
        result.setdefault("data", {})
        if isinstance(result["data"], dict):
            result["data"].setdefault("master_agent", state["master_agent"])
            result["data"].setdefault("worker_agent", state["current_worker_agent"])
        observation = _json_dumps(result)
        duration_ms = int((time.time() - state["step_started_at"]) * 1000)
        status = "success" if result.get("success") else "error"
        error = result.get("error")
        logged_input = json.loads(serialized_input) if serialized_input else state["current_safe_input"]
        logged_input["_master_agent"] = state["master_agent"]
        logged_input["_worker_agent"] = state["current_worker_agent"]
        approval_required = (
            result.get("mcp_error_code") == "MCP_APPROVAL_REQUIRED"
            or bool((result.get("data") or {}).get("approval_required"))
        )
        log = self._create_log(
            db=state["db"],
            agent_run_id=state["agent_run"].id,
            step=state["step"],
            decision=state["current_decision"],
            raw_decision=state["current_raw"],
            tool_name=state["current_tool_name"],
            input_params=logged_input,
            observation="" if approval_required else observation,
            output_result=observation,
            status="pending_approval" if approval_required else status,
            error=error,
            duration_ms=duration_ms,
        )
        await self._emit_event(
            state.get("event_callback"),
            {
                "type": "step_completed",
                "run_id": state["agent_run"].id,
                "log": self.serialize_log(log),
                "master_agent": state["master_agent"],
                "worker_agent": state["current_worker_agent"],
            },
        )
        if approval_required:
            approval_request_id = (result.get("data") or {}).get("approval_request_id")
            awaiting_run = self._save_workflow_snapshot(
                state,
                node="awaiting_approval",
                status="awaiting_approval",
                result=_json_dumps(
                    self._build_awaiting_approval_payload(
                        agent_run_id=state["agent_run"].id,
                        db=state["db"],
                        user_id=state["user_id"],
                        master_agent=state["master_agent"],
                        worker_agent=state["worker_agent"],
                        approval_request_id=int(approval_request_id or 0),
                        tool_name=state["current_tool_name"],
                        max_steps=state["max_steps"],
                        worker_plan=state.get("worker_plan"),
                        handoffs=state.get("handoffs"),
                        supervisor_plan_details=state.get("supervisor_plan"),
                    )
                ),
                final_answer="执行已暂停，等待人工审批。",
                failure_reason=None,
                total_steps=state["step"],
            )
            await self._emit_event(
                state.get("event_callback"),
                {
                    "type": "run_waiting_approval",
                    "run": self.serialize_run(awaiting_run),
                    "approval_request_id": approval_request_id,
                    "tool_name": state["current_tool_name"],
                },
            )
            state["final_run"] = awaiting_run
            state["awaiting_approval"] = True
            return state
        if state["current_tool_name"] in EVIDENCE_SOURCE_TOOLS and result.get("success"):
            state["evidence_scope_seen"] = True
        state["last_observation"] = observation
        self._save_workflow_snapshot(
            state,
            node="decide",
            last_observation=observation,
            failure_reason=_sanitize_agent_error_message(error),
            total_steps=state["step"],
        )
        self._append_observation(state["messages"], state["current_raw"], observation)
        return state

    async def _workflow_awaiting_approval(self, state: dict[str, Any]) -> dict[str, Any]:
        return state

    async def _workflow_partial(self, state: dict[str, Any]) -> dict[str, Any]:
        partial_answer = "已达到最大执行步数，任务部分完成。"
        self._save_workflow_snapshot(state, node="partial")
        result_run = self._finalize_completed_run(
            db=state["db"],
            agent_run=state["agent_run"],
            final_answer=partial_answer,
            last_observation=state["last_observation"],
            failure_reason=state["agent_run"].failure_reason,
            total_steps=state["max_steps"],
            master_agent=state["master_agent"],
            worker_agent=state["worker_agent"],
            run_started=state["run_started"],
            summary_status="partial",
            error_message=state["agent_run"].failure_reason,
            worker_plan=state.get("worker_plan"),
            handoffs=state.get("handoffs"),
            supervisor_plan_details=state.get("supervisor_plan"),
        )
        await self._emit_event(
            state.get("event_callback"),
            {
                "type": "run_completed",
                "run": self.serialize_run(result_run),
                "master_agent": state["master_agent"],
                "worker_agent": state["worker_agent"],
            },
        )
        state["final_run"] = result_run
        return state

    async def run(
        self,
        goal: str,
        user_id: int,
        db: Session,
        session_id: int | None = None,
        max_steps: int = 5,
        event_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> AgentRun:
        agent_run = self._create_run(goal=goal, user_id=user_id, session_id=session_id, db=db)
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
        result, serialized_input = await self._execute_tool(
            pending_log.tool_name,
            action_input,
            user_id,
            db,
            agent_type=execution_agent,
            agent_run_id=agent_run.id,
            skip_approval=True,
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
        query = db.query(AgentRun).filter(AgentRun.id == run_id)
        if user_id is not None:
            query = query.filter(AgentRun.user_id == user_id)
        return query.first()

    def request_cancel(self, run_id: int, *, db: Session, user_id: int, reason: str | None = None) -> AgentRun:
        run = self.get_run(run_id, db, user_id=user_id)
        if not run:
            raise ValueError("Agent run not found")
        if run.status not in {"running", "awaiting_approval", "cancelling"}:
            raise ValueError("Agent run is not active")
        run.cancel_requested_at = utc_now()
        run.cancel_reason = (reason or "").strip() or None
        if run.status == "awaiting_approval":
            run.status = "cancelled"
            run.final_answer = "执行已取消，未恢复待审批操作。"
            run.failure_reason = "cancelled_by_user"
            run.completed_at = utc_now()
        else:
            run.status = "cancelling"
        return self._save_run(db, run)

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
        query = db.query(ToolCallLog).join(AgentRun).filter(ToolCallLog.agent_run_id == run_id)
        if user_id is not None:
            query = query.filter(AgentRun.user_id == user_id)
        return query.order_by(ToolCallLog.step.asc(), ToolCallLog.created_at.asc()).all()

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
            "meeting": ("meetings", "meeting_id"),
            "task": ("tasks", "task_id"),
            "email": ("emails", "draft_id"),
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
