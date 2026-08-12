"""Agent-Tool permission matrix.

Every MCP tool call is checked against the agent's allowed-tool list
before execution.  This module extracts the matrix that was formerly
embedded in ``agent_service.SUB_AGENTS`` into a separate, testable layer.
"""

from __future__ import annotations

from typing import Sequence

# Canonical runtime roles. Legacy names remain accepted at the MCP boundary
# so paused runs and stored approval requests can still resume safely.
CANONICAL_AGENT_TYPES = (
    "knowledge_agent",
    "legal_compliance_agent",
    "workflow_agent",
)

LEGACY_AGENT_ALIASES = {
    "document_agent": "knowledge_agent",
    "task_agent": "workflow_agent",
}


def canonical_agent_type(agent_type: str) -> str:
    """Return the current runtime role for a legacy or canonical role name."""
    return LEGACY_AGENT_ALIASES.get(agent_type, agent_type)


# ── Permission matrix ──────────────────────────────────────────────────
# Maps canonical role keys to the set of atomic MCP tools they may invoke.
AGENT_TOOL_ALLOW: dict[str, set[str]] = {
    "knowledge_agent": {
        "document_search_tool",
        "document_summary_tool",
        "document_risk_tool",
        "document_conflict_tool",
    },
    "legal_compliance_agent": {
        "document_search_tool",
        "document_summary_tool",
        "document_risk_tool",
        "document_conflict_tool",
        "legal_consultation_tool",
        "legal_contract_review_tool",
        "legal_draft_tool",
    },
    "workflow_agent": {
        "task_create_tool",
        "task_query_tool",
    },
    # Compatibility-only role. It is excluded from Supervisor planning.
    "general_agent": {
        "document_search_tool",
        "document_summary_tool",
        "document_risk_tool",
        "document_conflict_tool",
        "task_create_tool",
        "task_query_tool",
        "sql_query_tool",
        "legal_consultation_tool",
        "legal_contract_review_tool",
        "legal_draft_tool",
    },
}

# Keep legacy MCP callers narrowly scoped to their previous tool sets. These
# aliases are compatibility entries, not roles exposed to the Supervisor.
AGENT_TOOL_ALLOW.update(
    {
        "document_agent": set(AGENT_TOOL_ALLOW["knowledge_agent"]),
        "task_agent": {"task_create_tool", "task_query_tool"},
    }
)

# Pseudo-action "tools" that bypass the permission check.
_BYPASS_TOOLS = frozenset({"finish", "retry"})


def agent_allows_tool(agent_type: str, tool_name: str) -> bool:
    """Check whether ``agent_type`` may call ``tool_name``."""
    if tool_name in _BYPASS_TOOLS:
        return True
    allowed = AGENT_TOOL_ALLOW.get(agent_type)
    if allowed is None:
        return False
    return tool_name in allowed


def allowed_tools_for(agent_type: str) -> set[str]:
    """Return the set of tool names ``agent_type`` is allowed to call."""
    return AGENT_TOOL_ALLOW.get(agent_type, set())


def resolve_agent_for_tool(
    tool_name: str,
    fallback_agent: str,
) -> str:
    """Resolve which agent type owns ``tool_name``.

    If ``tool_name`` is a bypass action or already allowed by
    ``fallback_agent``, returns ``fallback_agent`` unchanged.
    Otherwise scans the matrix for the owning agent.
    """
    canonical_fallback = canonical_agent_type(fallback_agent)
    if tool_name in _BYPASS_TOOLS:
        return canonical_fallback
    if tool_name in AGENT_TOOL_ALLOW.get(canonical_fallback, set()):
        return canonical_fallback
    for agent_type in CANONICAL_AGENT_TYPES:
        tools = AGENT_TOOL_ALLOW[agent_type]
        if tool_name in tools:
            return agent_type
    return canonical_fallback


def all_agent_types() -> Sequence[str]:
    """Return every registered agent type key."""
    return list(AGENT_TOOL_ALLOW.keys())
