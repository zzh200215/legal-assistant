"""Agent Run 具类型状态、集中状态机与错误分类。

- ``AgentPlan`` / ``AgentRunState``：取代无约束的大型 dict 承载计划、执行步骤、工具调用、
  审批、重试、取消、截止时间、证据与错误。可序列化、可持久化、可按 run_id 恢复。
- ``RunStateMachine``：集中校验合法状态转移，沿用现有状态字符串（running / awaiting_approval /
  completed / error / cancelled / cancelling），前端与 API 契约零破坏。
- 安全默认：未知状态不可转移；cancel_requested 在步骤边界与工具执行前检查。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core.time import utc_now

# ── 状态词表（沿用现有 API 可见字符串）──────────────────────────────────────────
STATUS_RUNNING = "running"
STATUS_AWAITING_APPROVAL = "awaiting_approval"
STATUS_COMPLETED = "completed"
STATUS_ERROR = "error"
STATUS_CANCELLING = "cancelling"
STATUS_CANCELLED = "cancelled"

RUN_ACTIVE_STATUSES = frozenset({STATUS_RUNNING, STATUS_AWAITING_APPROVAL, STATUS_CANCELLING})
RUN_TERMINAL_STATUSES = frozenset({STATUS_COMPLETED, STATUS_ERROR, STATUS_CANCELLED})

# 合法转移表。未列出的转移一律拒绝。
RUN_TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_RUNNING: frozenset(
        {STATUS_AWAITING_APPROVAL, STATUS_COMPLETED, STATUS_ERROR, STATUS_CANCELLING}
    ),
    STATUS_AWAITING_APPROVAL: frozenset({STATUS_RUNNING, STATUS_CANCELLED, STATUS_ERROR}),
    STATUS_CANCELLING: frozenset({STATUS_CANCELLED, STATUS_ERROR}),
    STATUS_ERROR: frozenset({STATUS_RUNNING}),
    STATUS_COMPLETED: frozenset(),
    STATUS_CANCELLED: frozenset(),
}


class IllegalRunTransition(ValueError):
    """非法状态转移。"""


class RunStateMachine:
    @staticmethod
    def transition(status: str, target: str) -> str:
        """校验并返回目标状态；非法转移抛 IllegalRunTransition。"""
        allowed = RUN_TRANSITIONS.get(status, frozenset())
        if target not in allowed:
            raise IllegalRunTransition(
                f"Illegal Agent run transition: {status} -> {target}"
            )
        return target

    @staticmethod
    def can_cancel(status: str) -> bool:
        return status in RUN_ACTIVE_STATUSES

    @staticmethod
    def is_active(status: str) -> bool:
        return status in RUN_ACTIVE_STATUSES


# ── 错误分类 ──────────────────────────────────────────────────────────────────
ERROR_CATEGORY_PERMISSION = "permission_denied"
ERROR_CATEGORY_VALIDATION = "validation"
ERROR_CATEGORY_NOT_FOUND = "not_found"
ERROR_CATEGORY_TIMEOUT = "timeout"
ERROR_CATEGORY_TRANSIENT = "transient"
ERROR_CATEGORY_AUTHZ_CHANGED = "authz_changed"
ERROR_CATEGORY_CANCELLED = "cancelled"
ERROR_CATEGORY_SIDE_EFFECT = "side_effect"
ERROR_CATEGORY_UNKNOWN = "unknown"

# 暂时性错误类别：执行器可据此按重试策略重试。
RETRYABLE_CATEGORIES = frozenset({ERROR_CATEGORY_TRANSIENT, ERROR_CATEGORY_TIMEOUT})


def classify_error(error: str | None, mcp_error_code: str | None = None) -> str:
    if mcp_error_code == "MCP_APPROVAL_REQUIRED":
        return "approval_required"
    if mcp_error_code == "MCP_PERMISSION_DENIED":
        return ERROR_CATEGORY_PERMISSION
    if mcp_error_code == "MCP_VALIDATION_ERROR":
        return ERROR_CATEGORY_VALIDATION
    if mcp_error_code == "MCP_TOOL_NOT_FOUND":
        return ERROR_CATEGORY_NOT_FOUND
    if mcp_error_code == "AUTHZ_CHANGED":
        return ERROR_CATEGORY_AUTHZ_CHANGED
    if mcp_error_code == "AGENT_TOOL_TIMEOUT":
        return ERROR_CATEGORY_TIMEOUT
    # 工具实现抛出异常 → MCP_INTERNAL_ERROR：视为暂时性基础设施错误，
    # 仅在工具显式声明 retryable=True 时才被重试（默认工具不重试）。
    if mcp_error_code == "MCP_INTERNAL_ERROR":
        return ERROR_CATEGORY_TRANSIENT
    return ERROR_CATEGORY_UNKNOWN


# ── 结构化计划 ────────────────────────────────────────────────────────────────

@dataclass
class AgentPlanStep:
    step: int
    tool_name: str
    purpose: str
    action_input_preview: dict[str, Any] = field(default_factory=dict)
    read_only: bool = True
    requires_approval: bool = False


@dataclass
class AgentPlan:
    intent: str
    workers: list[str]
    dependencies: list[dict[str, str]]
    risk_level: str
    expected_artifacts: list[str]
    execution_mode: str
    rationale: str
    plan_source: str
    fallback_reason: str | None = None
    parallel_plan: dict[str, Any] | None = None
    steps: list[AgentPlanStep] = field(default_factory=list)
    requires_approval: bool = False
    selected_skill: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "workers": list(self.workers),
            "dependencies": [dict(item) for item in self.dependencies],
            "risk_level": self.risk_level,
            "expected_artifacts": list(self.expected_artifacts),
            "execution_mode": self.execution_mode,
            "rationale": self.rationale,
            "plan_source": self.plan_source,
            "fallback_reason": self.fallback_reason,
            "parallel_plan": self.parallel_plan,
            "steps": [
                {
                    "step": item.step,
                    "tool_name": item.tool_name,
                    "purpose": item.purpose,
                    "action_input_preview": item.action_input_preview,
                    "read_only": item.read_only,
                    "requires_approval": item.requires_approval,
                }
                for item in self.steps
            ],
            "requires_approval": self.requires_approval,
            "selected_skill": self.selected_skill,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "AgentPlan | None":
        if not isinstance(payload, dict):
            return None
        steps = []
        for raw in payload.get("steps") or []:
            if not isinstance(raw, dict):
                continue
            steps.append(
                AgentPlanStep(
                    step=int(raw.get("step") or 0),
                    tool_name=str(raw.get("tool_name") or ""),
                    purpose=str(raw.get("purpose") or ""),
                    action_input_preview=(
                        raw.get("action_input_preview")
                        if isinstance(raw.get("action_input_preview"), dict)
                        else {}
                    ),
                    read_only=bool(raw.get("read_only", True)),
                    requires_approval=bool(raw.get("requires_approval", False)),
                )
            )
        return cls(
            intent=str(payload.get("intent") or "general_legal_request"),
            workers=[str(item) for item in (payload.get("workers") or [])],
            dependencies=[
                dict(item) for item in (payload.get("dependencies") or []) if isinstance(item, dict)
            ],
            risk_level=str(payload.get("risk_level") or "medium"),
            expected_artifacts=[str(item) for item in (payload.get("expected_artifacts") or [])],
            execution_mode=str(payload.get("execution_mode") or "sequential"),
            rationale=str(payload.get("rationale") or ""),
            plan_source=str(payload.get("plan_source") or "unknown"),
            fallback_reason=payload.get("fallback_reason"),
            parallel_plan=(
                payload.get("parallel_plan") if isinstance(payload.get("parallel_plan"), dict) else None
            ),
            steps=steps,
            requires_approval=bool(payload.get("requires_approval", False)),
            selected_skill=(
                payload.get("selected_skill") if isinstance(payload.get("selected_skill"), dict) else None
            ),
        )


# ── 步骤与工具调用记录 ─────────────────────────────────────────────────────────

@dataclass
class ToolCallRecord:
    tool_name: str
    tool_version: str = "1"
    status: str = "pending"                 # pending_approval | success | error | cancelled | skipped
    error_category: str | None = None
    input_hash: str | None = None
    idempotency_key: str | None = None
    permission_decision: str | None = None
    timeout_ms: int | None = None
    retried_of: str | None = None
    compensation_status: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in self.__dict__.items()}

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "ToolCallRecord | None":
        if not isinstance(payload, dict):
            return None
        return cls(**{key: payload[key] for key in cls.__dataclass_fields__ if key in payload})


@dataclass
class StepRecord:
    step_index: int
    worker_agent: str
    action_type: str = "tool_call"
    tool_name: str | None = None
    status: str = "pending"                 # pending | running | success | error | awaiting_approval
    attempts: int = 0
    error_category: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    deadline: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in self.__dict__.items()}

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "StepRecord | None":
        if not isinstance(payload, dict):
            return None
        return cls(**{key: payload[key] for key in cls.__dataclass_fields__ if key in payload})


# ── 审批状态（供 run 内嵌快照，实际持久化在 agent_approval_requests）────────────

@dataclass
class ApprovalState:
    request_id: int | None = None
    status: str = "pending"
    step_id: int | None = None
    tool_name: str | None = None
    param_digest: str | None = None
    expires_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in self.__dict__.items()}

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "ApprovalState | None":
        if not isinstance(payload, dict):
            return None
        return cls(**{key: payload[key] for key in cls.__dataclass_fields__ if key in payload})


# ── Run 具类型状态 ────────────────────────────────────────────────────────────

@dataclass
class AgentRunState:
    run_id: int
    user_id: int
    status: str = STATUS_RUNNING
    node: str = "decide"
    step: int = 0
    trace_id: str | None = None
    organization_id: int | None = None
    plan: AgentPlan | None = None
    executed_steps: list[StepRecord] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    approval: ApprovalState | None = None
    retry_count: int = 0
    cancel_requested: bool = False
    run_deadline_at: str | None = None
    evidence: dict[str, Any] | None = None
    error_category: str | None = None
    compensation_status: str | None = None

    # ── 生命周期辅助 ──
    def mark_cancel_requested(self) -> None:
        self.cancel_requested = True

    def is_expired(self, now: datetime | None = None) -> bool:
        if not self.run_deadline_at:
            return False
        try:
            deadline = datetime.fromisoformat(self.run_deadline_at)
        except ValueError:
            return False
        return (now or utc_now()) > deadline

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": 1,
            "run_id": self.run_id,
            "user_id": self.user_id,
            "status": self.status,
            "node": self.node,
            "step": self.step,
            "trace_id": self.trace_id,
            "organization_id": self.organization_id,
            "plan": self.plan.to_dict() if self.plan else None,
            "executed_steps": [item.to_dict() for item in self.executed_steps],
            "tool_calls": [item.to_dict() for item in self.tool_calls],
            "approval": self.approval.to_dict() if self.approval else None,
            "retry_count": self.retry_count,
            "cancel_requested": self.cancel_requested,
            "run_deadline_at": self.run_deadline_at,
            "evidence": self.evidence,
            "error_category": self.error_category,
            "compensation_status": self.compensation_status,
        }

    @classmethod
    def from_snapshot(cls, payload: dict[str, Any] | None) -> "AgentRunState | None":
        if not isinstance(payload, dict) or "run_id" not in payload:
            return None
        return cls(
            run_id=int(payload.get("run_id") or 0),
            user_id=int(payload.get("user_id") or 0),
            status=str(payload.get("status") or STATUS_RUNNING),
            node=str(payload.get("node") or "decide"),
            step=int(payload.get("step") or 0),
            trace_id=payload.get("trace_id"),
            organization_id=payload.get("organization_id"),
            plan=AgentPlan.from_dict(payload.get("plan")),
            executed_steps=[
                item
                for raw in (payload.get("executed_steps") or [])
                if (item := StepRecord.from_dict(raw))
            ],
            tool_calls=[
                item
                for raw in (payload.get("tool_calls") or [])
                if (item := ToolCallRecord.from_dict(raw))
            ],
            approval=ApprovalState.from_dict(payload.get("approval")),
            retry_count=int(payload.get("retry_count") or 0),
            cancel_requested=bool(payload.get("cancel_requested", False)),
            run_deadline_at=payload.get("run_deadline_at"),
            evidence=(
                payload.get("evidence") if isinstance(payload.get("evidence"), dict) else None
            ),
            error_category=payload.get("error_category"),
            compensation_status=payload.get("compensation_status"),
        )
