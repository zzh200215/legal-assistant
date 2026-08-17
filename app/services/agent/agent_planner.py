"""Planner：生成结构化执行计划（AgentPlan）。只规划、不执行任何工具。

- 依据目标、Worker 能力与角色 ACL 路由 Worker，生成依赖、风险等级、预期产物。
- 计划为结构化对象（见 app.services.agent.agent_run_state.AgentPlan），包含步骤、工具、
  输入摘要、预期副作用与是否需要审批。
- 规则回退与 Supervisor-LLM 计划校验沿用既有逻辑，产出的 supervisor_plan dict 形状不变，
  保证 AgentService 兼容层与前端契约零破坏。
"""

from __future__ import annotations

import re
from typing import Any

from app.mcp.permissions import (
    CANONICAL_AGENT_TYPES,
    canonical_agent_type,
)
from app.mcp.registry import mcp_registry
from app.services.agent.agent_json import json_loads_dict as _json_loads_dict
from app.services.agent.agent_prompts import (
    PARALLEL_READ_ONLY_WORKER_PAIRS,
    SUB_AGENT_DESCRIPTIONS,
    SUPERVISOR_ARTIFACT_TYPES,
    SUPERVISOR_RISK_LEVELS,
)
from app.services.agent.agent_registry import AGENT_REGISTRY_VERSION, TASK_PROTOCOL_VERSION
from app.services.agent.agent_run_state import AgentPlan, AgentPlanStep
from app.services.agent.agent_skill_registry import resolve_agent_skill
from app.services.llm.llm_service import llm_service
from app.services.llm.prompt_service import prompt_service

# workflow_agent 是受控副作用层：出现在计划中即代表存在写步骤，需要审批。
_WRITE_WORKERS = frozenset({"workflow_agent"})


def _has_document_intent(goal: str, normalized: str) -> bool:
    return any(
        keyword in goal or keyword in normalized
        for keyword in ("文档", "合同", "方案", "document", "risk", "冲突", "核对", "对比", "conflict")
    )


def _has_legal_intent(goal: str, normalized: str) -> bool:
    return any(keyword in goal or keyword in normalized for keyword in ("合同", "条款", "合规", "法务", "违约", "审查"))


def _has_task_intent(goal: str, normalized: str) -> bool:
    return any(keyword in goal or keyword in normalized for keyword in ("任务", "待办", "task", "todo"))


def build_worker_plan(goal: str) -> list[str]:
    """规则路由：按领域关键词生成有序 Worker 计划。"""
    normalized = (goal or "").lower()
    plan: list[str] = []
    if _has_legal_intent(goal, normalized):
        plan.append("legal_compliance_agent")
    if _has_document_intent(goal, normalized) and not _has_legal_intent(goal, normalized):
        plan.append("knowledge_agent")
    if _has_task_intent(goal, normalized):
        plan.append("workflow_agent")
    if not plan:
        if _has_document_intent(goal, normalized):
            plan.append("knowledge_agent")
        else:
            plan.append("knowledge_agent")
    return plan


class Planner:
    """结构化计划生成器。不持有工具，不执行工具。"""

    def plan_worker(self, goal: str) -> str:
        workers = build_worker_plan(goal)
        return workers[0]

    @staticmethod
    def _can_parallelize(workers: list[str]) -> bool:
        canonical = {canonical_agent_type(worker) for worker in workers}
        return len(workers) == 2 and frozenset(canonical) in PARALLEL_READ_ONLY_WORKER_PAIRS

    def _parallel_plan(self, goal: str, workers: list[str]) -> dict[str, Any] | None:
        if not self._can_parallelize(workers):
            return None
        match = re.search(r"(?:文档|合同|方案|document)\s*(?:id)?\s*(\d+)", goal, flags=re.IGNORECASE)
        if not match:
            return None
        document_id = int(match.group(1))
        tool_name = "document_risk_tool" if ("风险" in goal or "risk" in goal.lower()) else "document_summary_tool"
        knowledge = next((w for w in workers if canonical_agent_type(w) == "knowledge_agent"), "knowledge_agent")
        legal = next((w for w in workers if canonical_agent_type(w) == "legal_compliance_agent"), "legal_compliance_agent")
        return {
            knowledge: {"tool_name": tool_name, "action_input": {"document_id": document_id}},
            legal: {"tool_name": "document_risk_tool", "action_input": {"document_id": document_id}},
        }

    def _fallback_dict(self, goal: str, *, reason: str | None = None) -> dict[str, Any]:
        workers = build_worker_plan(goal)
        normalized = (goal or "").lower()
        expected_artifacts = list(dict.fromkeys([
            item for item in (
                ("document" if "knowledge_agent" in workers or "legal_compliance_agent" in workers else None),
                ("task" if _has_task_intent(goal, normalized) else None),
            )
            if item
        ]))
        parallel_plan = self._parallel_plan(goal, workers)
        return {
            "intent": (goal or "").strip() or "general_legal_request",
            "workers": workers,
            "dependencies": [
                {"from": workers[index], "to": workers[index + 1]} for index in range(len(workers) - 1)
            ],
            "risk_level": "medium" if "workflow_agent" in workers else "low",
            "expected_artifacts": expected_artifacts,
            "rationale": "使用规则路由生成稳定的最小 Worker 计划。",
            "plan_source": "rule_fallback",
            "fallback_reason": reason,
            "execution_mode": "parallel_read_only" if parallel_plan else "sequential",
            "parallel_plan": parallel_plan,
            "architecture_version": AGENT_REGISTRY_VERSION,
            "guardrail_nodes": ["rbac", "tool_acl", "approval", "evidence_verification"],
        }

    def _validate_dict(self, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        allowed_workers = set(CANONICAL_AGENT_TYPES) | {"document_agent", "task_agent"}
        workers = payload.get("workers")
        if not isinstance(workers, list) or not workers or len(workers) > 4:
            return None, "workers 必须是 1 到 4 个 Worker 的列表"
        requested = [str(item).strip() for item in workers]
        if any(item not in allowed_workers for item in requested):
            return None, "计划包含未知或内部 Worker"
        normalized_workers = [canonical_agent_type(item) for item in requested]
        if len(set(normalized_workers)) != len(normalized_workers):
            return None, "Worker 不允许重复"

        dependencies = payload.get("dependencies") or []
        if not isinstance(dependencies, list):
            return None, "dependencies 必须是列表"
        normalized_deps = []
        for dependency in dependencies:
            if not isinstance(dependency, dict):
                return None, "dependency 必须是对象"
            source = canonical_agent_type(str(dependency.get("from") or "").strip())
            target = canonical_agent_type(str(dependency.get("to") or "").strip())
            if source not in normalized_workers or target not in normalized_workers:
                return None, "dependency 指向计划外 Worker"
            if normalized_workers.index(source) >= normalized_workers.index(target):
                return None, "dependency 必须从前序 Worker 指向后序 Worker"
            normalized_deps.append({"from": source, "to": target})

        risk_level = str(payload.get("risk_level") or "medium").strip().lower()
        if risk_level not in SUPERVISOR_RISK_LEVELS:
            return None, "risk_level 非法"
        artifacts = payload.get("expected_artifacts") or []
        if not isinstance(artifacts, list):
            return None, "expected_artifacts 必须是列表"
        normalized_artifacts = [str(item).strip().lower() for item in artifacts if str(item).strip()]
        if any(item not in SUPERVISOR_ARTIFACT_TYPES for item in normalized_artifacts):
            return None, "expected_artifacts 包含非法类型"

        return {
            "intent": str(payload.get("intent") or "general_legal_request").strip() or "general_legal_request",
            "workers": normalized_workers,
            "dependencies": normalized_deps,
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

    async def plan_dict(self, goal: str, user_id: int) -> dict[str, Any]:
        """返回 supervisor_plan dict（兼容形状）。单领域直接路由，不调用 Supervisor LLM。"""
        workers = build_worker_plan(goal)
        if len(workers) == 1:
            plan = self._fallback_dict(goal)
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
            "agent_supervisor_plan", user_id=user_id, sub_agent_descriptions=SUB_AGENT_DESCRIPTIONS, goal=goal,
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
            return self._fallback_dict(goal, reason="supervisor_generation_failed")
        payload = llm_service.parse_json_object(raw)
        plan, error = self._validate_dict(payload)
        if plan:
            if plan["workers"] != workers:
                return self._fallback_dict(goal, reason="supervisor_role_boundary_mismatch")
            parallel_plan = self._parallel_plan(goal, plan["workers"])
            if parallel_plan:
                plan["execution_mode"] = "parallel_read_only"
                plan["parallel_plan"] = parallel_plan
            return plan
        return self._fallback_dict(goal, reason=error or "supervisor_plan_invalid")

    async def plan(self, goal: str, user_id: int) -> AgentPlan:
        """生成结构化 AgentPlan（唯一公共入口，不执行工具）。"""
        raw = await self.plan_dict(goal, user_id)
        steps: list[AgentPlanStep] = []
        for index, worker in enumerate(raw.get("workers") or [], start=1):
            is_write = canonical_agent_type(worker) in _WRITE_WORKERS
            steps.append(
                AgentPlanStep(
                    step=index,
                    tool_name=worker,
                    purpose=f"{worker} 执行阶段",
                    action_input_preview={},
                    read_only=not is_write,
                    requires_approval=is_write,
                )
            )
        requires_approval = any(step.requires_approval for step in steps)
        return AgentPlan(
            intent=str(raw.get("intent") or "general_legal_request"),
            workers=[canonical_agent_type(item) for item in (raw.get("workers") or [])],
            dependencies=[dict(item) for item in (raw.get("dependencies") or [])],
            risk_level=str(raw.get("risk_level") or "medium"),
            expected_artifacts=list(raw.get("expected_artifacts") or []),
            execution_mode=str(raw.get("execution_mode") or "sequential"),
            rationale=str(raw.get("rationale") or ""),
            plan_source=str(raw.get("plan_source") or "unknown"),
            fallback_reason=raw.get("fallback_reason"),
            parallel_plan=raw.get("parallel_plan"),
            steps=steps,
            requires_approval=requires_approval,
            selected_skill=resolve_agent_skill(goal),
        )


planner = Planner()
