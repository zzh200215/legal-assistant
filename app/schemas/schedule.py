from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ScheduledWorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    workflow_type: str
    frequency: str = "daily"
    run_time: str = "09:00"
    weekday: int | None = Field(default=None, ge=0, le=6)
    timezone: str = "Asia/Shanghai"
    config: dict = Field(default_factory=dict)


class ScheduledWorkflowUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    run_time: str | None = None
    weekday: int | None = Field(default=None, ge=0, le=6)
    enabled: bool | None = None
    config: dict | None = None


class ScheduledWorkflowOut(BaseModel):
    id: int
    user_id: int
    organization_id: int | None = None
    department_id: int | None = None
    name: str
    workflow_type: str
    frequency: str
    run_time: str
    weekday: int | None = None
    timezone: str
    config: dict = Field(default_factory=dict)
    enabled: bool
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_status: str | None = None
    consecutive_failures: int
    created_at: datetime
    updated_at: datetime


class WorkflowExecutionOut(BaseModel):
    id: int
    schedule_id: int
    user_id: int
    trigger_type: str
    status: str
    scheduled_for: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result_summary: str | None = None
    result_detail: dict = Field(default_factory=dict)
    error_message: str | None = None
    retry_count: int
    created_at: datetime
    updated_at: datetime
