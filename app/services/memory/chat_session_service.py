"""聊天会话服务（P1 API 统一化）：WS/REST 共用的会话与消息持久化。

Route 层禁止直接操作 ORM：ws_chat 原在 handler 内创建 ChatSession/ChatMessage，
统一下沉到本服务（get_or_create_session / add_message），与会话记忆、QA 记录
保持同一套持久化来源。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.chat import ChatMessage, ChatSession


class ChatSessionService:
    def get_or_create_session(
        self,
        db: Session,
        *,
        user_id: int,
        session_id: int | None = None,
        title: str = "",
        session_type: str = "general",
    ) -> ChatSession:
        """按 session_id 取既有会话（须属于当前用户）或创建新会话。"""
        if session_id:
            session = (
                db.query(ChatSession)
                .filter(ChatSession.id == session_id, ChatSession.user_id == user_id)
                .first()
            )
            if session:
                return session
        session = ChatSession(
            user_id=user_id,
            title=(title or "")[:50],
            session_type=session_type,
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        return session

    def add_message(
        self,
        db: Session,
        *,
        session_id: int,
        role: str,
        content: str,
    ) -> ChatMessage:
        message = ChatMessage(session_id=session_id, role=role, content=content)
        db.add(message)
        db.commit()
        db.refresh(message)
        return message

    def history_before(self, db: Session, *, session_id: int, before_message_id: int,
                       limit: int = 200) -> list[dict]:
        """本消息之前的会话上文（role/content 列表）。"""
        rows = (
            db.query(ChatMessage)
            .filter(
                ChatMessage.session_id == session_id,
                ChatMessage.id < before_message_id,
            )
            .order_by(ChatMessage.id.asc())
            .limit(limit)
            .all()
        )
        return [{"role": m.role, "content": m.content} for m in rows]


chat_session_service = ChatSessionService()
