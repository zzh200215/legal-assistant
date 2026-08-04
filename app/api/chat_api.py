from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.core.api_response import api_error, should_passthrough_exception
from app.core.database import get_db
from app.core.auth import get_current_user
from app.models.user import User
from app.services.document_qa_service import document_qa_service
from app.services.document_service import document_service
from app.services.agentic_rag_service import agentic_rag_service
from app.services.llm_service import llm_service

router = APIRouter()


class ChatMessage(BaseModel):
    role: str
    content: str = Field(min_length=1, max_length=8000)

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        allowed = {"user", "assistant", "system"}
        if value not in allowed:
            raise ValueError("role 必须是 user、assistant 或 system")
        return value


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=30)
    document_id: int | None = None


@router.post("/")
async def chat(
    req: ChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """通用聊天，如果传了 document_id 则基于文档 RAG 问答"""
    try:
        if req.document_id is not None:
            doc = document_service.get(req.document_id, db, user_id=current_user.id)
            if not doc:
                raise api_error(404, "文档不存在", code="DOCUMENT_NOT_FOUND")
            last_message = req.messages[-1].content if req.messages else ""
            result = await agentic_rag_service.answer_async(
                last_message,
                document_id=req.document_id,
                user_id=current_user.id,
            )
            qa_record = document_qa_service.record(
                document_id=req.document_id,
                user_id=current_user.id,
                question=last_message,
                answer=result["answer"],
                db=db,
                citations=result["citations"],
                hit_chunks=result["hit_chunks"],
                latency_ms=result["latency_ms"],
                source="chat",
            )
            return {
                "qa_record_id": qa_record.id,
                "role": "assistant",
                "content": result["answer"],
                "citations": result["citations"],
                "confidence": result["confidence"],
                "can_answer": result["can_answer"],
                "refusal_reason": result.get("refusal_reason"),
                "agentic_rag": result.get("agentic_rag"),
            }
        else:
            messages = [{"role": m.role, "content": m.content} for m in req.messages]
            answer = await llm_service.chat(messages, action="chat", user_id=current_user.id)
            return {"role": "assistant", "content": answer}
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "聊天请求失败", code="CHAT_REQUEST_FAILED", detail=str(e))
