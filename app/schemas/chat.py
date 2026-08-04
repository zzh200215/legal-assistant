from pydantic import BaseModel, ConfigDict
from datetime import datetime


class ChatSessionBase(BaseModel):
    title: str | None = None
    session_type: str = "general"


class ChatSessionCreate(ChatSessionBase):
    pass


class ChatSessionOut(ChatSessionBase):
    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ChatMessageBase(BaseModel):
    role: str
    content: str


class ChatMessageCreate(ChatMessageBase):
    pass


class ChatMessageOut(ChatMessageBase):
    id: int
    session_id: int
    tokens_used: int | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
