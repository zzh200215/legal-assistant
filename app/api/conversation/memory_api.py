from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.api_response import api_error, should_passthrough_exception
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.services.memory.conversation_memory_service import conversation_memory_service


router = APIRouter()


class PreferenceCreateRequest(BaseModel):
    category: str = Field(default="general", min_length=1, max_length=64)
    preference_key: str = Field(min_length=1, max_length=128)
    preference_value: str = Field(min_length=1, max_length=1000)


class PreferenceOut(BaseModel):
    id: int
    category: str
    preference_key: str
    preference_value: str
    source: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SessionMemoryOut(BaseModel):
    session_id: int
    summary: str | None = None
    summarized_through_message_id: int | None = None
    updated_at: datetime | None = None


@router.get("/preferences", response_model=list[PreferenceOut])
def list_preferences(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return conversation_memory_service.list_preferences(db, current_user.id)


@router.post("/preferences", response_model=PreferenceOut)
def save_preference(
    req: PreferenceCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return conversation_memory_service.save_explicit_preference(
            db,
            current_user.id,
            category=req.category,
            preference_key=req.preference_key,
            preference_value=req.preference_value,
        )
    except Exception as exc:
        if should_passthrough_exception(exc):
            raise
        raise api_error(400, "保存偏好失败", code="PREFERENCE_SAVE_FAILED", detail=str(exc))


@router.delete("/preferences/{preference_id}")
def delete_preference(
    preference_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not conversation_memory_service.delete_preference(db, current_user.id, preference_id):
        raise api_error(404, "偏好不存在", code="PREFERENCE_NOT_FOUND")
    return {"success": True}


@router.post("/sessions/{session_id}/long-term-extract", response_model=list[PreferenceOut])
async def extract_long_term_preferences(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await conversation_memory_service.extract_long_term_preferences(
        db,
        current_user.id,
        session_id,
    )


@router.get("/sessions/{session_id}", response_model=SessionMemoryOut)
def get_session_memory(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    memory = conversation_memory_service.get_session_memory(db, current_user.id, session_id)
    if memory is None:
        from app.models.chat import ChatSession

        owned = db.query(ChatSession.id).filter(ChatSession.id == session_id, ChatSession.user_id == current_user.id).first()
        if not owned:
            raise api_error(404, "会话不存在或无权访问", code="SESSION_NOT_FOUND")
        return SessionMemoryOut(session_id=session_id)
    return SessionMemoryOut(
        session_id=session_id,
        summary=memory.summary,
        summarized_through_message_id=memory.summarized_through_message_id,
        updated_at=memory.updated_at,
    )
