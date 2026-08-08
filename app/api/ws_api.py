import json
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.core.auth import get_settings, get_user_from_token
from app.core.database import get_db
from app.core.llm_client import llm_client
from app.models.chat import ChatSession, ChatMessage
from app.models.user import User
from app.services.agent_service import agent_service
from app.services.document_qa_service import document_qa_service
from app.services.document_service import document_service
from app.services.agentic_rag_service import agentic_rag_service
from app.services.conversation_memory_service import conversation_memory_service

router = APIRouter()
settings = get_settings()
MAX_WS_MESSAGE_LENGTH = 8000
MAX_AGENT_STEPS = 10
WS_BEARER_PROTOCOL_PREFIX = "bearer."


async def _send_ws_json(websocket: WebSocket, payload: dict):
    await websocket.send_text(json.dumps(payload, ensure_ascii=False))


def _ws_error_message(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        message = detail.get("message") or detail.get("detail")
        if isinstance(message, str) and message:
            return message
    return "请求失败"


def _load_ws_user(token: str, db: Session) -> User | None:
    return get_user_from_token(token, db)


def _extract_ws_auth(websocket: WebSocket) -> tuple[str | None, str | None]:
    protocol_header = websocket.headers.get("sec-websocket-protocol", "")
    for protocol in [item.strip() for item in protocol_header.split(",") if item.strip()]:
        if protocol.startswith(WS_BEARER_PROTOCOL_PREFIX):
            token = protocol[len(WS_BEARER_PROTOCOL_PREFIX) :].strip()
            if token:
                return token, protocol
    return None, None


@router.websocket("/ws/chat")
async def ws_chat(
    websocket: WebSocket,
    db: Session = Depends(get_db),
):
    token, selected_protocol = _extract_ws_auth(websocket)
    if not token or not selected_protocol:
        await websocket.close(code=1008)
        return
    await websocket.accept(subprotocol=selected_protocol)

    user = _load_ws_user(token, db)
    if not user:
        await websocket.send_json({"type": "error", "content": "认证失败"})
        await websocket.close()
        return

    try:
        while True:
            data = await websocket.receive_json()
            content = data.get("content", "")
            document_id = data.get("document_id")
            session_id = data.get("session_id")

            if not content.strip():
                continue
            if len(content) > MAX_WS_MESSAGE_LENGTH:
                await websocket.send_json({"type": "error", "content": f"消息长度不能超过 {MAX_WS_MESSAGE_LENGTH} 字符"})
                continue

            # 获取或创建会话
            session = None
            if session_id:
                session = db.query(ChatSession).filter(ChatSession.id == session_id, ChatSession.user_id == user.id).first()
            if not session:
                session = ChatSession(
                    user_id=user.id,
                    title=content[:50],
                    # RAG answers stay in the document domain and are not compacted into chat memory.
                    session_type="document_rag" if document_id else "general",
                )
                db.add(session)
                db.commit()
                db.refresh(session)

            # 保存用户消息
            user_msg = ChatMessage(session_id=session.id, role="user", content=content)
            db.add(user_msg)
            db.commit()

            # 流式响应
            if document_id:
                doc = document_service.get(document_id, db, user_id=user.id)
                if not doc:
                    await websocket.send_json({"type": "error", "content": "文档不存在或无权访问"})
                    continue
                result = await agentic_rag_service.answer_async(
                    content,
                    document_id=document_id,
                    user_id=user.id,
                    knowledge_base_id=doc.knowledge_base_id,
                )
                chunks = result["hit_chunks"]
                citations = result["citations"]
                full_response = result["answer"]
                await websocket.send_json({"type": "session", "session_id": session.id})
                await websocket.send_json(
                    {
                        "type": "done",
                        "content": full_response,
                        "citations": citations,
                        "confidence": result["confidence"],
                        "can_answer": result["can_answer"],
                        "refusal_reason": result.get("refusal_reason"),
                        "agentic_rag": result.get("agentic_rag"),
                    }
                )
            else:
                # The memory service injects a bounded session summary and explicit user preferences.
                messages = conversation_memory_service.build_chat_messages(db, user.id, session.id)
                chunks = []
                citations = []
                # 发送会话信息
                await websocket.send_json({"type": "session", "session_id": session.id})

                # 流式输出
                full_response = ""
                async for chunk in llm_client.chat_stream(messages, action="chat_stream", user_id=user.id):
                    full_response += chunk
                    await websocket.send_json({"type": "chunk", "content": chunk})

                # 发送完成信号
                await websocket.send_json({"type": "done", "content": full_response})

            # 保存助手消息
            assistant_msg = ChatMessage(session_id=session.id, role="assistant", content=full_response)
            db.add(assistant_msg)
            db.commit()

            if not document_id:
                await conversation_memory_service.compact_session_if_needed(db, user.id, session.id)

            if document_id:
                document_qa_service.record(
                    document_id=document_id,
                    user_id=user.id,
                    session_id=session.id,
                    question=content,
                    answer=full_response,
                    db=db,
                    citations=citations,
                    hit_chunks=chunks,
                    source="ws_chat",
                )

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "content": _ws_error_message(e)})
        except Exception:
            pass


@router.websocket("/ws/agent")
async def ws_agent(
    websocket: WebSocket,
    db: Session = Depends(get_db),
):
    token, selected_protocol = _extract_ws_auth(websocket)
    if not token or not selected_protocol:
        await websocket.close(code=1008)
        return
    await websocket.accept(subprotocol=selected_protocol)

    user = _load_ws_user(token, db)
    if not user:
        await _send_ws_json(websocket, {"type": "error", "message": "认证失败"})
        await websocket.close()
        return

    try:
        while True:
            data = await websocket.receive_json()
            action = str(data.get("action") or "run").strip()

            async def event_callback(payload: dict):
                await _send_ws_json(websocket, payload)

            if action == "resume_approval":
                approval_id = data.get("approval_id")
                try:
                    approval_id = int(approval_id)
                except (TypeError, ValueError):
                    await _send_ws_json(websocket, {"type": "error", "message": "approval_id 必须为整数"})
                    continue
                run = await agent_service.resume_after_approval(
                    approval_id=approval_id,
                    user_id=user.id,
                    db=db,
                    event_callback=event_callback,
                )
            else:
                goal = (data.get("goal") or "").strip()
                raw_max_steps = data.get("max_steps") or 5
                session_id = data.get("session_id")

                if not goal:
                    await _send_ws_json(websocket, {"type": "error", "message": "目标不能为空"})
                    continue
                if len(goal) > MAX_WS_MESSAGE_LENGTH:
                    await _send_ws_json(websocket, {"type": "error", "message": f"目标长度不能超过 {MAX_WS_MESSAGE_LENGTH} 字符"})
                    continue
                try:
                    max_steps = int(raw_max_steps)
                except (TypeError, ValueError):
                    await _send_ws_json(websocket, {"type": "error", "message": "max_steps 必须为整数"})
                    continue
                if max_steps < 1 or max_steps > MAX_AGENT_STEPS:
                    await _send_ws_json(websocket, {"type": "error", "message": f"max_steps 必须在 1 到 {MAX_AGENT_STEPS} 之间"})
                    continue

                run = await agent_service.run(
                    goal=goal,
                    user_id=user.id,
                    db=db,
                    session_id=session_id,
                    max_steps=max_steps,
                    event_callback=event_callback,
                )
            logs = [agent_service.serialize_log(item) for item in agent_service.get_run_logs(run.id, db, user_id=user.id)]
            await _send_ws_json(
                websocket,
                {
                    "type": "run_snapshot",
                    "run": agent_service.serialize_run(run),
                    "logs": logs,
                },
            )
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await _send_ws_json(websocket, {"type": "error", "message": _ws_error_message(e)})
        except Exception:
            pass
