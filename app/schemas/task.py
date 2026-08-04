from pydantic import BaseModel, ConfigDict
from datetime import datetime


class TaskBase(BaseModel):
    title: str
    description: str | None = None
    assignee: str | None = None
    collaborators: list[str] = []
    priority: str = "medium"
    progress: int = 0
    due_date: datetime | None = None


class TaskCreate(TaskBase):
    pass


class TaskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    assignee: str | None = None
    collaborators: list[str] | None = None
    status: str | None = None
    priority: str | None = None
    progress: int | None = None
    due_date: datetime | None = None


class TaskOut(TaskBase):
    id: int
    user_id: int
    organization_id: int | None = None
    department_id: int | None = None
    status: str
    source_type: str | None = None
    source_id: int | None = None
    parent_id: int | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TaskCommentCreate(BaseModel):
    content: str


class TaskCommentOut(BaseModel):
    id: int
    task_id: int
    user_id: int
    content: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TaskLogOut(BaseModel):
    id: int
    task_id: int
    action: str
    detail: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
