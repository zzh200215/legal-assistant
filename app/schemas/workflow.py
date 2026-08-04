from datetime import date

from pydantic import BaseModel, Field


class MeetingTaskSelection(BaseModel):
    source_index: int = Field(ge=0)
    selected: bool = True
    title: str | None = None
    description: str | None = None
    assignee: str | None = None
    due_date: str | None = None
    priority: str | None = None


class MeetingTaskConfirmRequest(BaseModel):
    items: list[MeetingTaskSelection] = Field(default_factory=list)


class WeeklyReportDraftRequest(BaseModel):
    scope: str = "mine"
    start_date: date | None = None
    end_date: date | None = None
    recipient: str | None = None
    title: str | None = None


class RiskFollowupDraftRequest(BaseModel):
    recipient: str | None = None
