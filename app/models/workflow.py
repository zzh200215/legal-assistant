"""Product-facing workflow runs and transactional event outbox."""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func

from app.core.database import Base


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (UniqueConstraint("task_id", name="uq_workflow_runs_task_id"),)

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    task_id = Column(String(128), nullable=True, index=True)
    workflow_type = Column(String(64), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    case_id = Column(Integer, ForeignKey("legal_cases.id"), nullable=True, index=True)
    business_key = Column(String(256), nullable=True, index=True)
    idempotency_key = Column(String(128), nullable=True, index=True)
    status = Column(String(32), nullable=False, default="queued", index=True)
    current_step = Column(String(64), nullable=True)
    progress = Column(Integer, nullable=False, default=0)
    attempt = Column(Integer, nullable=False, default=0)
    error_code = Column(String(64), nullable=True)
    error_message = Column(String(1000), nullable=True)
    checkpoint_json = Column(Text, nullable=True)
    cancel_requested_at = Column(DateTime(timezone=True), nullable=True)
    cancel_reason = Column(String(256), nullable=True)
    cancel_requested_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    trace_id = Column(String(64), nullable=True, index=True)
    request_id = Column(String(64), nullable=True, index=True)
    queued_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WorkflowEvent(Base):
    __tablename__ = "workflow_events"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    workflow_run_id = Column(Integer, ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    from_status = Column(String(32), nullable=True)
    to_status = Column(String(32), nullable=True)
    step = Column(String(64), nullable=True)
    progress = Column(Integer, nullable=True)
    payload_json = Column(Text, nullable=True)
    actor_type = Column(String(24), nullable=False, default="system")
    actor_id = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class WorkflowOutboxEvent(Base):
    __tablename__ = "workflow_outbox_events"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    workflow_event_id = Column(Integer, ForeignKey("workflow_events.id", ondelete="CASCADE"), nullable=False, unique=True)
    event_type = Column(String(64), nullable=False, index=True)
    status = Column(String(16), nullable=False, default="pending", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    available_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    claim_owner = Column(String(64), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(String(512), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
