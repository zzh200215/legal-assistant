from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func

from app.core.database import Base


class ScheduledWorkflow(Base):
    __tablename__ = "scheduled_workflows"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    department_id = Column(Integer, ForeignKey("departments.id"), nullable=True, index=True)
    name = Column(String(128), nullable=False)
    workflow_type = Column(String(64), nullable=False, index=True)
    frequency = Column(String(32), nullable=False, default="daily")
    run_time = Column(String(5), nullable=False, default="09:00")
    weekday = Column(Integer, nullable=True)
    timezone = Column(String(64), nullable=False, default="Asia/Shanghai")
    config_json = Column(Text, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    next_run_at = Column(DateTime(timezone=True), nullable=True, index=True)
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    last_status = Column(String(32), nullable=True)
    consecutive_failures = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WorkflowExecution(Base):
    __tablename__ = "workflow_executions"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_workflow_executions_idempotency_key"),)

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    schedule_id = Column(Integer, ForeignKey("scheduled_workflows.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    trigger_type = Column(String(32), nullable=False, default="scheduled")
    idempotency_key = Column(String(256), nullable=False)
    status = Column(String(32), nullable=False, default="pending", index=True)
    scheduled_for = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    celery_task_id = Column(String(128), nullable=True, index=True)
    result_summary = Column(Text, nullable=True)
    result_detail_json = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
