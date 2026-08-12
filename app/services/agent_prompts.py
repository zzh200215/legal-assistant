"""Agent prompt/planning building blocks (extracted from agent_service.py).

Module-level, state-free helpers used by AgentService: tool description
templates, agent metadata, decision normalization, and prompt rendering.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.mcp.permissions import CANONICAL_AGENT_TYPES, allowed_tools_for, canonical_agent_type
from app.mcp.registry import mcp_registry
from app.services.agent_json import extract_json_object as _extract_json_object
from app.services.agent_registry import AGENT_REGISTRY
from app.services.prompt_service import prompt_service


# ── Canonical domain-agent definitions ─────────────────────────────────
# These are kept here for prompt-building (label, description) and goal
# routing.  The actual tool-permission matrix lives in
# app.mcp.permissions.AGENT_TOOL_ALLOW — always consult that module for
# enforcement.

SUB_AGENTS = {
    agent_type: {**config, "tools": tuple(sorted(allowed_tools_for(agent_type)))}
    for agent_type, config in AGENT_REGISTRY.items()
}


def build_tool_descriptions(tool_names: tuple[str, ...] | list[str] | None = None) -> str:
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


TOOL_DESCRIPTIONS = build_tool_descriptions()
SUB_AGENT_DESCRIPTIONS = _build_sub_agent_descriptions()

PRIORITY_FLOWS = (
    "- 总结文档并提取风险：document_summary_tool -> document_risk_tool -> finish\n"
    "- 审查合同并生成文书草稿：legal_contract_review_tool -> legal_draft_tool -> finish\n"
    "- 法律咨询并检索法源：legal_consultation_tool -> document_search_tool -> finish"
)

EVIDENCE_SOURCE_TOOLS = {
    "document_search_tool",
    "document_summary_tool",
    "document_risk_tool",
    "document_conflict_tool",
}

EVIDENCE_GATED_WRITE_TOOLS = {
    "task_create_tool",
}

# Only these tools may run in the concurrent fan-out. The list intentionally
# excludes all side effects and tools that can open an approval workflow.
PARALLEL_READ_ONLY_TOOLS = {
    "document_search_tool",
    "document_summary_tool",
    "document_risk_tool",
    "document_conflict_tool",
}
PARALLEL_READ_ONLY_WORKER_PAIRS = {
    frozenset({"knowledge_agent", "legal_compliance_agent"}),
}
PARALLEL_READ_ONLY_WORKERS = {"knowledge_agent", "legal_compliance_agent"}

SUPERVISOR_ARTIFACT_TYPES = {"document", "task"}
SUPERVISOR_RISK_LEVELS = {"low", "medium", "high"}
POLICY_GUARDRAIL_ROLE = "policy_guardrail"


def sanitize_agent_error_message(error: str | None) -> str | None:
    if not error:
        return None
    if error in {"Invalid JSON response", "Agent 决策要求重试"}:
        return error
    if error.startswith("Invalid action_type:"):
        return error
    return "Agent 执行失败，请查看系统日志"


def normalize_decision(raw: str) -> dict[str, Any]:
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


def goal_execution_hints(goal: str) -> str:
    normalized = goal.lower()
    hints: list[str] = []

    if ("文档" in goal or "document" in normalized) and ("风险" in goal or "risk" in normalized):
        hints.append("建议先用 document_summary_tool，再用 document_risk_tool，最后 finish。")

    if "合同" in goal or "contract" in normalized:
        hints.append("合同类目标优先使用 legal_contract_review_tool，需要草稿时再用 legal_draft_tool。")

    if "咨询" in goal or "法律" in goal or "legal" in normalized:
        hints.append("法律咨询目标优先使用 legal_consultation_tool，并结合 document_search_tool 检索法源。")

    if ("冲突" in goal or "核对" in goal or "对比" in goal or "conflict" in normalized):
        hints.append("涉及多份文档的日期、金额或负责人冲突时，使用 document_conflict_tool，并依据返回的原文定位汇总结论。")

    if not hints:
        hints.append("请优先选择最少但有效的工具步骤，完成后及时 finish。")

    return "\n".join(hints)


def build_demo_plan_preview(goal: str, max_steps: int) -> dict[str, Any] | None:
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


def build_worker_system_prompt(worker_name: str, user_id: int | None = None) -> str:
    worker_name = canonical_agent_type(worker_name)
    worker = SUB_AGENTS.get(worker_name) or SUB_AGENTS["knowledge_agent"]
    scoped_descriptions = build_tool_descriptions(worker["tools"])
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


def build_preview_prompt(goal: str, user_id: int | None = None) -> str:
    return prompt_service.render_by_name(
        "agent_plan_preview",
        user_id=user_id,
        tool_descriptions=TOOL_DESCRIPTIONS,
        priority_flows=PRIORITY_FLOWS,
        sub_agent_descriptions=SUB_AGENT_DESCRIPTIONS,
        goal=goal,
        execution_hints=goal_execution_hints(goal),
    )
