from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AgentRunRequest(BaseModel):
    goal: str
    session_id: int | None = None
    max_steps: int = Field(default=5, ge=1, le=10)


class AgentPlanPreviewRequest(BaseModel):
    goal: str
    max_steps: int = Field(default=5, ge=1, le=10)


class AgentPlanPreviewStep(BaseModel):
    step: int
    tool_name: str
    purpose: str
    action_input_preview: dict = Field(default_factory=dict)


class AgentPlanPreviewResponse(BaseModel):
    summary: str
    estimated_steps: int
    steps: list[AgentPlanPreviewStep] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    can_execute: bool = True
    selected_skill: dict | None = None


class ToolCallLogOut(BaseModel):
    id: int
    agent_run_id: int
    step: int | None = None
    action_type: str | None = None
    thought: str | None = None
    tool_name: str
    input_params: str | None = None
    raw_decision: str | None = None
    observation: str | None = None
    output_result: str | None = None
    status: str
    error: str | None = None
    duration_ms: int | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AgentRunOut(BaseModel):
    id: int
    user_id: int
    session_id: int | None = None
    goal: str
    status: str
    result: str | None = None
    final_answer: str | None = None
    artifacts: dict = Field(default_factory=dict)
    supervisor_plan: dict = Field(default_factory=dict)
    last_observation: str | None = None
    failure_reason: str | None = None
    total_steps: int | None = None
    error: str | None = None
    created_at: datetime
    completed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class AgentRunDetailOut(AgentRunOut):
    logs: list[ToolCallLogOut] = Field(default_factory=list)


class AgentRunHistoryOut(BaseModel):
    id: int
    goal: str
    status: str
    result: str | None = None
    final_answer: str | None = None
    failure_reason: str | None = None
    total_steps: int | None = None
    created_at: datetime
    completed_at: datetime | None = None


class AgentRunResponse(BaseModel):
    run_id: int
    status: str
    result: str | None = None
    final_answer: str | None = None
    artifacts: dict = Field(default_factory=dict)
    supervisor_plan: dict = Field(default_factory=dict)
    failure_reason: str | None = None
    error: str | None = None
    logs: list[ToolCallLogOut] = Field(default_factory=list)


class AgentApprovalDecisionRequest(BaseModel):
    approved: bool
    decision_note: str | None = None


class AgentApprovalResumeRequest(BaseModel):
    decision_note: str | None = None


class AgentRunCancelRequest(BaseModel):
    reason: str | None = None


class AgentApprovalRequestOut(BaseModel):
    id: int
    agent_run_id: int | None = None
    user_id: int
    tool_name: str
    agent_type: str | None = None
    input_params: str | None = None
    risk_level: str
    status: str
    approval_token: str
    decision_note: str | None = None
    created_at: datetime
    decided_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
