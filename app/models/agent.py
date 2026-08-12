from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func
from app.core.database import Base


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id"), nullable=True, index=True)
    goal = Column(Text, nullable=False)
    status = Column(String(32), default="running", nullable=False)
    result = Column(Text, nullable=True)
    workflow_state = Column(Text, nullable=True)
    workflow_state_updated_at = Column(DateTime(timezone=True), nullable=True)
    cancel_requested_at = Column(DateTime(timezone=True), nullable=True)
    cancel_reason = Column(Text, nullable=True)
    final_answer = Column(Text, nullable=True)
    last_observation = Column(Text, nullable=True)
    failure_reason = Column(Text, nullable=True)
    total_steps = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)
    # 长流程权限快照：Agent 执行期间权限范围保持稳定，硬撤销立即终止。
    authorization_snapshot_id = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)


class ToolCallLog(Base):
    __tablename__ = "tool_call_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    agent_run_id = Column(Integer, ForeignKey("agent_runs.id"), nullable=False, index=True)
    step = Column(Integer, nullable=True)
    action_type = Column(String(32), nullable=True)
    thought = Column(Text, nullable=True)
    tool_name = Column(String(128), nullable=False)
    input_params = Column(Text, nullable=True)
    raw_decision = Column(Text, nullable=True)
    observation = Column(Text, nullable=True)
    output_result = Column(Text, nullable=True)
    status = Column(String(32), default="success", nullable=False)
    error = Column(Text, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AgentApprovalRequest(Base):
    __tablename__ = "agent_approval_requests"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    agent_run_id = Column(Integer, ForeignKey("agent_runs.id"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    tool_name = Column(String(128), nullable=False, index=True)
    agent_type = Column(String(64), nullable=True, index=True)
    input_params = Column(Text, nullable=True)
    risk_level = Column(String(32), default="high", nullable=False)
    status = Column(String(32), default="pending", nullable=False, index=True)
    approval_token = Column(String(128), nullable=False, unique=True, index=True)
    decision_note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    decided_at = Column(DateTime(timezone=True), nullable=True)
