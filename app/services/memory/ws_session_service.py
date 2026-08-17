"""WebSocket 会话协议服务（P1 API 统一化）。

职责：
- 会话注册与身份绑定（user/org 来自已认证连接，resume token 为能力令牌）；
- 事件序号（seq 会话内单调递增）与出站队列（有界，背压控制）；
- 心跳（服务端 ping / 客户端任意消息视为活跃）与空闲超时；
- ack 与断线恢复：状态事件持久化到 ws_event_logs，resume 时按
  ``seq > ack_seq`` 补发；无法恢复明确返回 resync_required；
- 慢客户端处理：丢最旧 volatile 事件；仍超限 close 1013；
- 连接/恢复/背压/关闭全部写安全审计（可观测、可审计）。

约定：流式 chunk 等高频事件标记 volatile（不落库）；状态事件
（job_update / notification / run_snapshot 等）必须落库供恢复。
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── 协议常量（与 docs/websocket-protocol.md 保持一致）────────────────────────
MAX_OUTBOX = 500          # 每连接出站队列上限
MAX_EVENT_BYTES = 64 * 1024  # 单事件大小上限
SEND_TIMEOUT_SECONDS = 5.0   # 单次发送超时
PING_INTERVAL_SECONDS = 30.0  # 服务端心跳间隔
IDLE_TIMEOUT_SECONDS = 120.0  # 客户端空闲超时（无任何客户端消息）
RESUME_TTL_HOURS = 24         # resume token 有效期
WS_MESSAGE_MAX_BYTES = 128 * 1024  # 客户端消息大小上限

CLOSE_AUTH_FAILED = 1008
CLOSE_OVERLOADED = 1013
CLOSE_IDLE_TIMEOUT = 4001
CLOSE_RESUME_INVALID = 4002
CLOSE_PROTOCOL_ERROR = 4003

TERMINAL_CLOSE_CODES = {CLOSE_AUTH_FAILED, CLOSE_OVERLOADED, CLOSE_IDLE_TIMEOUT,
                        CLOSE_RESUME_INVALID, CLOSE_PROTOCOL_ERROR}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class WsSession:
    """单个 WebSocket 连接的会话状态（进程内；跨进程恢复依赖 ws_event_logs）。"""

    __slots__ = (
        "websocket", "user_id", "organization_id", "session_id", "resume_token",
        "seq", "acked_seq", "outbox", "channels", "created_at",
        "last_client_activity", "closed", "overloaded", "send_lock",
    )

    def __init__(self, websocket, *, user_id: int, organization_id: int | None):
        self.websocket = websocket
        self.user_id = user_id
        self.organization_id = organization_id
        self.session_id = uuid.uuid4().hex
        self.resume_token = secrets.token_urlsafe(24)
        self.seq = 0
        self.acked_seq = 0
        self.outbox: deque[tuple[dict[str, Any], bool]] = deque(maxlen=MAX_OUTBOX)
        self.channels: set[str] = set()
        self.created_at = _utcnow()
        self.last_client_activity = _utcnow()
        self.closed = False
        self.overloaded = False
        self.send_lock = asyncio.Lock()


def new_session(websocket, *, user_id: int, organization_id: int | None) -> WsSession:
    return WsSession(websocket, user_id=user_id, organization_id=organization_id)


def _envelope(session: WsSession, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    from app.core.obs_context import current_trace_id
    try:
        trace_id = current_trace_id() or ""
    except Exception:
        trace_id = ""
    return {
        "type": event_type,
        "seq": session.seq,
        "ts": _utcnow().isoformat(),
        "trace_id": trace_id,
        **payload,
    }


def _persist_event(db: Session, session: WsSession, event: dict[str, Any],
                   volatile: bool) -> None:
    """状态事件落库（断线恢复的持久化事件来源）；volatile 事件不落库。"""
    from app.models.ws_event_log import WsEventLog

    if volatile:
        return
    db.add(WsEventLog(
        session_id=session.session_id,
        resume_token=session.resume_token,
        user_id=session.user_id,
        organization_id=session.organization_id,
        channel=",".join(sorted(session.channels)) if session.channels else None,
        seq_no=event["seq"],
        event_type=event["type"],
        payload_json=json.dumps(event, ensure_ascii=False, default=str),
        volatile=0,
        expires_at=_utcnow() + timedelta(hours=RESUME_TTL_HOURS),
    ))
    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001 - 事件落库失败不阻断业务发送，仅记录
        db.rollback()
        logger.warning("ws event persist failed (session=%s seq=%s): %s: %s",
                       session.session_id, event["seq"], type(exc).__name__, exc)


async def send_event(
    session: WsSession,
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    volatile: bool = True,
    db: Session | None = None,
) -> bool:
    """发送事件：分配 seq → 背压队列 → 落库（状态事件）→ 发送。

    背压策略：队列满时丢弃最旧 volatile 事件；队列内无可丢弃事件
    （全为状态事件）→ 标记 overloaded（调用方应 close 1013）。
    返回是否成功发送；慢客户端发送失败时事件保留在队列。
    """
    if session.closed:
        return False
    session.seq += 1
    event = _envelope(session, event_type, payload or {})
    raw = json.dumps(event, ensure_ascii=False, default=str)
    if len(raw.encode("utf-8")) > MAX_EVENT_BYTES:
        logger.warning("ws event too large dropped (session=%s type=%s bytes=%d)",
                       session.session_id, event_type, len(raw))
        return False

    if len(session.outbox) >= MAX_OUTBOX:
        # 背压：重建队列丢弃最旧 volatile 事件；无可丢弃事件 → overloaded
        tmp: deque[tuple[dict[str, Any], bool]] = deque(maxlen=MAX_OUTBOX)
        dropped = False
        for queued, queued_volatile in session.outbox:
            if queued_volatile and not dropped:
                dropped = True
                continue
            tmp.append((queued, queued_volatile))
        if not dropped:
            session.overloaded = True
            logger.warning("ws outbox full of state events (session=%s) -> overloaded",
                           session.session_id)
            return False
        session.outbox = tmp

    if db is not None and not volatile:
        _persist_event(db, session, event, volatile=False)

    session.outbox.append((event, volatile))
    try:
        async with session.send_lock:
            await asyncio.wait_for(
                session.websocket.send_text(raw), timeout=SEND_TIMEOUT_SECONDS
            )
        return True
    except Exception:  # noqa: BLE001 - 慢客户端/断线：事件保留在 outbox 供背压策略处理
        if volatile:
            try:
                session.outbox.remove((event, True))
            except ValueError:
                pass
        return False


def mark_client_activity(session: WsSession) -> None:
    session.last_client_activity = _utcnow()


async def close_session(session: WsSession, code: int, reason: str,
                        db: Session | None = None) -> None:
    """关闭会话：释放 outbox、标记 closed、写安全审计。幂等。"""
    if session.closed:
        return
    session.closed = True
    session.outbox.clear()
    if db is not None:
        try:
            from app.services.org.security_audit_service import write_event
            write_event(
                event_type="ws_session",
                action="ws_close",
                actor_type="user",
                actor_id=str(session.user_id),
                result="closed",
                organization_id=session.organization_id,
                target_type="ws_session",
                target_id=session.session_id,
                reason_code=f"close_code:{code}",
                db=db,
            )
        except Exception:  # noqa: BLE001 - 审计写失败不阻断连接关闭
            db.rollback()
    try:
        await session.websocket.close(code=code, reason=reason)
    except Exception:  # noqa: BLE001 - 连接可能已断开
        pass


def resume_token_owner(db: Session, token: str, user_id: int) -> dict | None:
    """校验 resume token 归属与有效期。返回 ``{session_id, last_seq}`` 或 None。"""
    from app.models.ws_event_log import WsEventLog

    row = (
        db.query(WsEventLog)
        .filter(
            WsEventLog.resume_token == token,
            WsEventLog.user_id == user_id,
            WsEventLog.expires_at > _utcnow(),
        )
        .order_by(WsEventLog.seq_no.desc())
        .first()
    )
    if row is None:
        return None
    return {"session_id": row.session_id, "last_seq": row.seq_no}


def replay_events(db: Session, session_id: str, after_seq: int) -> list[dict]:
    """补发持久化事件（seq > after_seq，按序）。"""
    from app.models.ws_event_log import WsEventLog

    rows = (
        db.query(WsEventLog)
        .filter(
            WsEventLog.session_id == session_id,
            WsEventLog.seq_no > after_seq,
        )
        .order_by(WsEventLog.seq_no.asc())
        .all()
    )
    events = []
    for row in rows:
        try:
            events.append(json.loads(row.payload_json))
        except (TypeError, ValueError):
            continue
    return events
