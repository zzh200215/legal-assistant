"""Public metadata for the controlled Agent execution harness."""

from __future__ import annotations

from app.services.agent.agent_registry import AGENT_REGISTRY_VERSION
from app.services.agent.agent_skill_registry import SKILL_REGISTRY_VERSION
from app.workflows.langgraph_compat import workflow_engine_name


HARNESS_VERSION = "controlled_agent_harness_v1"


def get_harness_profile() -> dict:
    """Describe the server-enforced controls that govern every Agent run."""
    return {
        "harness_id": "controlled_agent_harness",
        "version": HARNESS_VERSION,
        "workflow_engine": workflow_engine_name(),
        "agent_registry_version": AGENT_REGISTRY_VERSION,
        "skill_registry_version": SKILL_REGISTRY_VERSION,
        "lifecycle": [
            "plan",
            "route",
            "tool_call",
            "evidence_verify",
            "approval_wait",
            "handoff_or_finish",
        ],
        "controls": [
            "role_scoped_mcp_acl",
            "bounded_steps",
            "evidence_gate_for_grounded_claims",
            "approval_for_side_effects",
            "cancellation_and_retry",
            "tool_call_audit_log",
        ],
    }
