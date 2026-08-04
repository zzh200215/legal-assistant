from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class EmailDraftBase(BaseModel):
    subject: str
    recipient: str | None = None
    cc: str | None = None
    content: str
    purpose: str | None = None
    key_points: list[str] = Field(default_factory=list)
    need_action: bool | None = None
    generation_type: str = "generate"
    original_email: str | None = None
    reply_goal: str | None = None
    tone: str = "professional"


class EmailDraftCreate(EmailDraftBase):
    pass


class EmailDraftOut(EmailDraftBase):
    id: int
    user_id: int
    organization_id: int | None = None
    department_id: int | None = None
    status: str
    metadata_json: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EmailGenerateRequest(BaseModel):
    purpose: str
    key_points: list[str] = Field(default_factory=list)
    tone: str = "professional"
    recipient: str | None = None
    need_action: bool = False


class TaskSyncEmailRequest(BaseModel):
    task_ids: list[int] = Field(default_factory=list)
    scope: str = "mine"
    tone: str = "professional"
    recipient: str | None = None
    include_overdue_only: bool = False
    need_action: bool = True
    purpose: str = "任务进度同步"


class EmailReplyRequest(BaseModel):
    original_email: str
    reply_goal: str
    tone: str = "professional"
    recipient: str | None = None


class EmailDraftActionResponse(BaseModel):
    draft: EmailDraftOut
    subject_candidates: list[str] = Field(default_factory=list)


class EmailSwitchToneRequest(BaseModel):
    target_tone: str


class EmailThreadSummaryRequest(BaseModel):
    emails: list[str]


class EmailThreadReplyRequest(BaseModel):
    emails: list[str]
    reply_goal: str
    tone: str = "professional"
    recipient: str | None = None


class EmailPolishRequest(BaseModel):
    instruction: str = "优化措辞，使其更专业"
