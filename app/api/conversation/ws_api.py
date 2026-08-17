"""WebSocket API — P1 协议化（/ws/chat、/ws/agent）。

协议契约见 docs/websocket-protocol.md（事件 envelope、seq/ack/resume/心跳/背压/取消）。
向后兼容保证：
- 认证方式不变：sec-websocket-protocol: bearer.<token>；
- 旧客户端消息格式继续可用：chat {content, document_id?, session_id?}、
  agent {action?, goal, max_steps?, session_id?, approval_id?}；
- 服务端事件新增 seq/ts/trace_id 字段（纯追加），旧客户端忽略即可。

新增能力：welcome/ping/ack/resume/resync_required/subscribe/cancel；
断线恢复使用 ws_event_logs 持久化状态事件（volatile 流式事件不落库）。
"""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.core.auth import get_settings
from app.core.database import get_db
from app.core.llm_client import llm_client
from app.models.user import User, UserStatus
from app.services.agent.agent_service import agent_service
from app.services.auth.auth_token_service import auth_token_service
from app.services.memory.chat_session_service import chat_session_service
from app.services.documents.document_qa_service import document_qa_service
from app.services.documents.document_service import document_service
from app.services.rag.agentic_rag_service import agentic_rag_service
from app.services.memory.conversation_memory_service import conversation_memory_service
from app.services.memory import ws_session_service as ws
from app.services.memory.ws_session_service import (
    WsSession,
    close_session,
    mark_client_activity,
    new_session,
    replay_events,
    resume_token_owner,
    send_event,
)

logger = logging.getLogger(__name__)

router = APIRouter()
settings = get_settings()
MAX_WS_MESSAGE_LENGTH = 8000
MAX_AGENT_STEPS = 10
WS_BEARER_PROTOCOL_PREFIX = "bearer."
INITIAL_MESSAGE_TIMEOUT_SECONDS = 30.0


# ── 认证（与 REST get_current_user 同套校验）──────────────────────────────────

def _load_ws_user(token: str, db: Session) -> User | None:
    """WebSocket 认证与 REST get_current_user 保持一致：
    校验签名/过期、jti 撤销、token_version，以及用户状态。
    deletion_pending 放行以支持注销流程，其余非 active 状态拒绝。
    """
    user = auth_token_service.validate_access_token(token, db)
    if user is None:
        return None
    if user.status == UserStatus.deletion_pending.value:
        return user
    if not user.is_active:
        return None
    return user


def _extract_ws_auth(websocket: WebSocket) -> tuple[str | None, str | None]:
    protocol_header = websocket.headers.get("sec-websocket-protocol", "")
    for protocol in [item.strip() for item in protocol_header.split(",") if item.strip()]:
        if protocol.startswith(WS_BEARER_PROTOCOL_PREFIX):
            token = protocol[len(WS_BEARER_PROTOCOL_PREFIX):].strip()
            if token:
                return token, protocol
    return None, None


def _ws_error_message(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        message = detail.get("message") or detail.get("detail")
        if isinstance(message, str) and message:
            return message
    return "请求失败"


def _write_ws_audit(db: Session, session: WsSession, action: str,
                    result: str, reason_code: str) -> None:
    """会话生命周期审计（open/resume/close/backpressure 均可观测）。失败不阻断连接。"""
    try:
        from app.services.org.security_audit_service import write_event
        write_event(
            event_type="ws_session",
            action=action,
            actor_type="user",
            actor_id=str(session.user_id),
            result=result,
            organization_id=session.organization_id,
            target_type="ws_session",
            target_id=session.session_id,
            reason_code=reason_code,
            db=db,
        )
    except Exception as exc:  # noqa: BLE001 - 审计失败不影响 WS 会话本身
        db.rollback()
        logger.warning("ws audit failed (%s): %s: %s", action, type(exc).__name__, exc)


# ── 连接建立：认证 → welcome / resume 补发 ────────────────────────────────────

async def _establish(websocket: WebSocket, db: Session) -> tuple[WsSession | None, dict | None, User | None]:
    token, selected_protocol = _extract_ws_auth(websocket)
    if not token or not selected_protocol:
        await websocket.close(code=ws.CLOSE_AUTH_FAILED)
        return None, None, None
    await websocket.accept(subprotocol=selected_protocol)

    user = _load_ws_user(token, db)
    if not user:
        await websocket.send_json({"type": "error", "content": "认证失败"})
        await websocket.close(code=ws.CLOSE_AUTH_FAILED)
        return None, None, None

    session = new_session(websocket, user_id=user.id, organization_id=user.organization_id)
    _write_ws_audit(db, session, "ws_open", "connected", "ws_open")

    try:
        raw = await asyncio.wait_for(
            websocket.receive_text(), timeout=INITIAL_MESSAGE_TIMEOUT_SECONDS
        )
    except Exception:  # noqa: BLE001 - 首条消息缺失/超时：协议错误关闭
        await close_session(session, ws.CLOSE_PROTOCOL_ERROR, "no initial message", db)
        return None, None, None
    mark_client_activity(session)

    try:
        msg = json.loads(raw)
    except ValueError:
        await send_event(session, "error",
                         {"code": "WS_PROTOCOL_ERROR", "message": "消息必须为 JSON"},
                         volatile=True)
        await close_session(session, ws.CLOSE_PROTOCOL_ERROR, "invalid json", db)
        return None, None, None

    if isinstance(msg, dict) and msg.get("type") == "resume":
        owner = resume_token_owner(db, str(msg.get("resume_token") or ""), user.id)
        if owner:
            try:
                ack_seq = int(msg.get("ack_seq") or 0)
            except (TypeError, ValueError):
                ack_seq = 0
            events = replay_events(db, owner["session_id"], ack_seq)
            session.seq = int(owner["last_seq"])
            await send_event(session, "welcome", {
                "session_id": session.session_id,
                "resume_token": session.resume_token,
                "last_seq": session.seq,
                "resumed": True,
            }, volatile=True)
            for event in events:
                try:
                    await session.websocket.send_text(
                        json.dumps(event, ensure_ascii=False, default=str)
                    )
                except Exception:  # noqa: BLE001 - 补发中断即止，客户端可再次 resume
                    break
            _write_ws_audit(db, session, "ws_resume", "resumed",
                            f"replayed:{len(events)}")
        else:
            # 无法恢复：welcome（resumed=False）后明确 resync_required，绝不静默丢事件
            await send_event(session, "welcome", {
                "session_id": session.session_id,
                "resume_token": session.resume_token,
                "last_seq": 0,
                "resumed": False,
            }, volatile=True)
            await send_event(session, "resync_required", {
                "reason": "invalid_or_expired_token",
                "last_seq": 0,
            }, volatile=True)
            _write_ws_audit(db, session, "ws_resume", "rejected", "resync_required")
        return session, None, user
    else:
        await send_event(session, "welcome", {
            "session_id": session.session_id,
            "resume_token": session.resume_token,
            "last_seq": 0,
            "resumed": False,
        }, volatile=True)
        return session, (msg if isinstance(msg, dict) else None), user


# ── 心跳 / 空闲超时 / 背压 ────────────────────────────────────────────────────

async def _heartbeat_loop(session: WsSession, db: Session) -> None:
    while not session.closed:
        await asyncio.sleep(ws.PING_INTERVAL_SECONDS)
        if session.closed:
            break
        idle_seconds = (ws._utcnow() - session.last_client_activity).total_seconds()
        if idle_seconds > ws.IDLE_TIMEOUT_SECONDS:
            _write_ws_audit(db, session, "ws_idle_timeout", "closed", "idle_timeout")
            await close_session(session, ws.CLOSE_IDLE_TIMEOUT, "idle timeout", db)
            break
        if session.overloaded:
            _write_ws_audit(db, session, "ws_backpressure", "closed", "overloaded")
            await close_session(session, ws.CLOSE_OVERLOADED, "backpressure overload", db)
            break
        await send_event(session, "ping", {}, volatile=True)


# ── 业务消息处理（chat / agent / cancel / 协议消息）───────────────────────────

async def _handle_chat(session: WsSession, data: dict, db: Session, user: User) -> None:
    content = data.get("content", "")
    document_id = data.get("document_id")
    session_id = data.get("session_id")

    if not isinstance(content, str) or not content.strip():
        await send_event(session, "error",
                         {"code": "WS_EMPTY_MESSAGE", "message": "消息内容不能为空"},
                         volatile=True)
        return
    if len(content) > MAX_WS_MESSAGE_LENGTH:
        await send_event(session, "error",
                         {"code": "WS_MESSAGE_TOO_LONG",
                          "message": f"消息长度不能超过 {MAX_WS_MESSAGE_LENGTH} 字符"},
                         volatile=True)
        return

    chat_session = chat_session_service.get_or_create_session(
        db,
        user_id=user.id,
        session_id=int(session_id) if isinstance(session_id, (int, str)) and str(session_id).isdigit() else None,
        title=content,
        session_type="document_rag" if document_id else "general",
    )
    user_msg = chat_session_service.add_message(
        db, session_id=chat_session.id, role="user", content=content,
    )

    citations = []
    chunks = []
    full_response = ""
    if document_id:
        doc = document_service.get(
            document_id,
            db,
            user_id=user.id,
            role=user.role,
            organization_id=user.organization_id,
            department_id=user.department_id,
        )
        if not doc:
            await send_event(session, "error",
                             {"code": "DOCUMENT_NOT_FOUND", "message": "文档不存在或无权访问"},
                             volatile=True)
            return
        history = chat_session_service.history_before(
            db, session_id=chat_session.id, before_message_id=user_msg.id,
        )
        result = await agentic_rag_service.answer_async(
            content,
            document_id=document_id,
            user_id=user.id,
            knowledge_base_id=doc.knowledge_base_id,
            authorized_document_ids=[document_id],
            conversation_history=history or None,
        )
        chunks = result["hit_chunks"]
        citations = result["citations"]
        full_response = result["answer"]
        await send_event(session, "session", {"session_id": chat_session.id},
                         volatile=False, db=db)
        await send_event(session, "done", {
            "content": full_response,
            "citations": citations,
            "confidence": result["confidence"],
            "can_answer": result["can_answer"],
            "refusal_reason": result.get("refusal_reason"),
            "agentic_rag": result.get("agentic_rag"),
        }, volatile=False, db=db)
    else:
        messages = conversation_memory_service.build_chat_messages(db, user.id, chat_session.id)
        await send_event(session, "session", {"session_id": chat_session.id},
                         volatile=False, db=db)
        async for chunk in llm_client.chat_stream(messages, action="chat_stream", user_id=user.id):
            full_response += chunk
            await send_event(session, "chunk", {"content": chunk}, volatile=True)
        await send_event(session, "done", {"content": full_response},
                         volatile=False, db=db)

    chat_session_service.add_message(
        db, session_id=chat_session.id, role="assistant", content=full_response,
    )
    if not document_id:
        await conversation_memory_service.compact_session_if_needed(db, user.id, chat_session.id)
    if document_id:
        document_qa_service.record(
            document_id=document_id,
            user_id=user.id,
            session_id=chat_session.id,
            question=content,
            answer=full_response,
            db=db,
            citations=citations,
            hit_chunks=chunks,
            source="ws_chat",
        )


async def _handle_agent(session: WsSession, data: dict, db: Session, user: User) -> None:
    action = str(data.get("action") or "run").strip()

    async def event_callback(payload: dict):
        await send_event(session, payload.get("type", "event"),
                         {k: v for k, v in payload.items() if k != "type"},
                         volatile=True)

    if action == "resume_approval":
        approval_id = data.get("approval_id")
        try:
            approval_id = int(approval_id)
        except (TypeError, ValueError):
            await send_event(session, "error",
                             {"code": "WS_INVALID_PARAM", "message": "approval_id 必须为整数"},
                             volatile=True)
            return
        run = await agent_service.resume_after_approval(
            approval_id=approval_id,
            user_id=user.id,
            db=db,
            event_callback=event_callback,
        )
    else:
        goal = (data.get("goal") or "").strip()
        try:
            raw_max_steps = data.get("max_steps") or 5
            max_steps = int(raw_max_steps)
        except (TypeError, ValueError):
            await send_event(session, "error",
                             {"code": "WS_INVALID_PARAM", "message": "max_steps 必须为整数"},
                             volatile=True)
            return
        session_id = data.get("session_id")
        if not goal:
            await send_event(session, "error",
                             {"code": "WS_EMPTY_GOAL", "message": "目标不能为空"},
                             volatile=True)
            return
        if len(goal) > MAX_WS_MESSAGE_LENGTH:
            await send_event(session, "error",
                             {"code": "WS_MESSAGE_TOO_LONG",
                              "message": f"目标长度不能超过 {MAX_WS_MESSAGE_LENGTH} 字符"},
                             volatile=True)
            return
        if max_steps < 1 or max_steps > MAX_AGENT_STEPS:
            await send_event(session, "error",
                             {"code": "WS_INVALID_PARAM",
                              "message": f"max_steps 必须在 1 到 {MAX_AGENT_STEPS} 之间"},
                             volatile=True)
            return

        run = await agent_service.run(
            goal=goal,
            user_id=user.id,
            db=db,
            session_id=session_id,
            max_steps=max_steps,
            event_callback=event_callback,
        )
    logs = [agent_service.serialize_log(item) for item in agent_service.get_run_logs(run.id, db, user_id=user.id)]
    await send_event(session, "run_snapshot", {
        "run": agent_service.serialize_run(run),
        "logs": logs,
    }, volatile=False, db=db)


async def _handle_cancel(session: WsSession, data: dict, db: Session, user: User) -> None:
    kind = data.get("kind")
    try:
        target_id = int(data.get("id"))
    except (TypeError, ValueError):
        await send_event(session, "error",
                         {"code": "WS_INVALID_PARAM", "message": "id 必须为整数"},
                         volatile=True)
        return

    if kind == "agent_run":
        try:
            run = agent_service.request_cancel(
                target_id, db=db, user_id=user.id, reason="ws_cancel",
            )
            await send_event(session, "cancelled", {
                "kind": "agent_run", "id": target_id, "status": run.status,
            }, volatile=False, db=db)
        except Exception as exc:  # noqa: BLE001 - 取消失败返回稳定错误
            await send_event(session, "error", {
                "code": "WS_CANCEL_FAILED", "message": _ws_error_message(exc),
            }, volatile=True)
        return

    if kind == "job":
        # 与 REST 取消端点一致的权限：仅组织管理员可取消组织内任务。
        from app.models.legal_platform import LegalAsyncJob
        from app.services.jobs.async_job_service import cancel_job
        from app.services.org.org_service import org_service

        member = (
            org_service.get_user_org_member(db=db, user_id=user.id, org_id=user.organization_id)
            if user.organization_id else None
        )
        if not member or member.legal_role != "admin":
            await send_event(session, "error",
                             {"code": "JOB_NOT_FOUND", "message": "任务不存在或无权访问"},
                             volatile=True)
            return
        job = db.query(LegalAsyncJob).filter(
            LegalAsyncJob.id == target_id,
            LegalAsyncJob.organization_id == user.organization_id,
        ).first()
        if not job:
            await send_event(session, "error",
                             {"code": "JOB_NOT_FOUND", "message": "任务不存在或无权访问"},
                             volatile=True)
            return
        result = cancel_job(db, job=job, actor_type="user",
                            actor_id=str(user.id), reason_code="ws_cancel")
        await send_event(session, "cancelled", {
            "kind": "job",
            "id": target_id,
            "cancelled": result["cancelled"],
            "job": result["job"],
        }, volatile=False, db=db)
        return

    await send_event(session, "error",
                     {"code": "WS_INVALID_PARAM", "message": f"未知取消类型: {kind}"},
                     volatile=True)


async def _handle_protocol_message(session: WsSession, msg: dict, db: Session,
                                   user: User) -> None:
    mark_client_activity(session)
    mtype = msg.get("type")
    if mtype == "ack":
        try:
            session.acked_seq = max(session.acked_seq, int(msg.get("ack_seq") or 0))
        except (TypeError, ValueError):
            pass
        return
    if mtype in ("pong",):
        return
    if mtype == "ping":
        await send_event(session, "pong", {}, volatile=True)
        return
    if mtype == "subscribe":
        channels = msg.get("channels") or []
        if isinstance(channels, list):
            session.channels.update(str(c) for c in channels if isinstance(c, str))
        await send_event(session, "subscribed",
                         {"channels": sorted(session.channels)}, volatile=True)
        return
    if mtype == "unsubscribe":
        channels = msg.get("channels") or []
        if isinstance(channels, list):
            session.channels.difference_update(str(c) for c in channels if isinstance(c, str))
        await send_event(session, "subscribed",
                         {"channels": sorted(session.channels)}, volatile=True)
        return
    if mtype == "cancel":
        await _handle_cancel(session, msg, db, user)
        return
    if mtype in (None, "chat", "agent_run"):
        if mtype == "agent_run" or "goal" in msg or "action" in msg:
            await _handle_agent(session, msg, db, user)
        else:
            await _handle_chat(session, msg, db, user)
        return
    await send_event(session, "error",
                     {"code": "WS_UNKNOWN_MESSAGE", "message": f"未知消息类型: {mtype}"},
                     volatile=True)


async def _run_session(session: WsSession, first_message: dict | None,
                       db: Session, user: User) -> None:
    heartbeat = asyncio.create_task(_heartbeat_loop(session, db))
    try:
        if first_message is not None:
            await _handle_protocol_message(session, first_message, db, user)
        while not session.closed:
            raw = await session.websocket.receive_text()
            try:
                msg = json.loads(raw)
            except ValueError:
                await send_event(session, "error",
                                 {"code": "WS_PROTOCOL_ERROR", "message": "消息必须为 JSON"},
                                 volatile=True)
                continue
            if isinstance(msg, dict):
                await _handle_protocol_message(session, msg, db, user)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001 - 业务异常按稳定错误发送，不泄露内部细节
        try:
            await send_event(session, "error",
                             {"code": "WS_INTERNAL_ERROR", "message": _ws_error_message(exc)},
                             volatile=True)
        except Exception:
            pass
    finally:
        await close_session(session, 1000, "closed", db)


# ── 端点 ──────────────────────────────────────────────────────────────────────

@router.websocket("/ws/chat")
async def ws_chat(
    websocket: WebSocket,
    db: Session = Depends(get_db),
):
    session, first, user = await _establish(websocket, db)
    if session is None or user is None:
        return
    await _run_session(session, first, db, user)


@router.websocket("/ws/agent")
async def ws_agent(
    websocket: WebSocket,
    db: Session = Depends(get_db),
):
    session, first, user = await _establish(websocket, db)
    if session is None or user is None:
        return
    await _run_session(session, first, db, user)
