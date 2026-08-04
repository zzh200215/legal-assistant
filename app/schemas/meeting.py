from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime


class MeetingBase(BaseModel):
    title: str


class MeetingCreate(MeetingBase):
    transcript: str | None = None
    audio_path: str | None = None


class MeetingOut(MeetingBase):
    id: int
    user_id: int
    organization_id: int | None = None
    department_id: int | None = None
    transcript: str | None = None
    transcript_source: str | None = None
    audio_path: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MeetingSummaryOut(BaseModel):
    theme: str | None = None
    topics: list[dict] | list[str] = Field(default_factory=list)
    decisions: list[dict] = Field(default_factory=list)
    action_items: list[dict] = Field(default_factory=list)
    risks: list[dict] = Field(default_factory=list)
    summary: str | None = None
    id: int
    meeting_id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
