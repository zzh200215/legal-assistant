from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ImapMailboxCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=993, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=512)
    mailbox: str = Field(default="INBOX", max_length=128)
    use_ssl: bool = True
    max_messages: int = Field(default=50, ge=1, le=200)
    important_senders: list[str] = Field(default_factory=list, max_length=100)


class MailboxMessageOut(BaseModel):
    id: int
    connector_id: int
    message_uid: str
    mailbox: str
    thread_id: str | None = None
    sender: str | None = None
    recipient: str | None = None
    subject: str | None = None
    summary: str | None = None
    category: str
    importance: str
    priority_score: int = 0
    received_at: datetime | None = None
    task_id: int | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MailboxTaskSuggestionOut(BaseModel):
    message_id: int
    title: str
    description: str | None = None
    priority: str
    already_created_task_id: int | None = None


class MailboxTaskConfirmRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    assignee: str | None = None
    priority: str = "medium"


class MailboxRetentionRequest(BaseModel):
    retention_days: int = Field(default=90, ge=7, le=3650)
    connector_id: int | None = None


class MailboxAutoReplyRequest(BaseModel):
    reply_goal: str = Field(default="确认收到，并说明将尽快跟进。", min_length=1, max_length=512)
    recipient: str | None = None
    smtp_connector_id: int | None = None
