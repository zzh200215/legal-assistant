"""站内通知实时推送端点（ux-audit M-9 中期项：铃铛 WS 实时红点）。

认证与 /ws/chat 同套（sec-websocket-protocol: bearer.<token>，与 REST get_current_user
同一校验链）。协议刻意保持最简：连接即注册，服务端只推 {type: "notification"} 与
pong，客户端断开由 onclose 重连 + 30s 轮询兜底，不做 seq/ack/resume（通知场景
丢一条可由轮询补齐，不值得协议复杂度）。
"""
import asyncio
import json
import logging

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User, UserStatus
from app.services.auth.auth_token_service import auth_token_service
from app.services.notification.notification_ws import notification_push

logger = logging.getLogger(__name__)

router = APIRouter()

WS_BEARER_PROTOCOL_PREFIX = "bearer."
CLOSE_AUTH_FAILED = 1008


def _extract_bearer_token(websocket: WebSocket) -> tuple[str | None, str | None]:
    protocol_header = websocket.headers.get("sec-websocket-protocol", "")
    for protocol in [item.strip() for item in protocol_header.split(",") if item.strip()]:
        if protocol.startswith(WS_BEARER_PROTOCOL_PREFIX):
            token = protocol[len(WS_BEARER_PROTOCOL_PREFIX):].strip()
            if token:
                return token, protocol
    return None, None


def _load_user(token: str, db: Session) -> User | None:
    """与 REST get_current_user 同套校验：签名/过期/jti/token_version/用户状态。"""
    user = auth_token_service.validate_access_token(token, db)
    if user is None:
        return None
    if user.status == UserStatus.deletion_pending.value:
        return user
    if not user.is_active:
        return None
    return user


@router.websocket("/ws/notifications")
async def ws_notifications(websocket: WebSocket, db: Session = Depends(get_db)):
    token, selected_protocol = _extract_bearer_token(websocket)
    if not token or not selected_protocol:
        await websocket.close(code=CLOSE_AUTH_FAILED)
        return
    # 回显客户端提议的完整协议串，保证 WS 握手 subprotocol 匹配
    await websocket.accept(subprotocol=selected_protocol)
    user = _load_user(token, db)
    if not user:
        await websocket.send_json({"type": "error", "message": "认证失败"})
        await websocket.close(code=CLOSE_AUTH_FAILED)
        return

    # 注册本进程事件循环：此后 notification_service 创建站内通知即可实时推送
    notification_push.register_loop(asyncio.get_running_loop())
    notification_push.connect(user.id, websocket)
    try:
        await websocket.send_json({"type": "ready"})
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except ValueError:
                await websocket.send_json({"type": "error", "message": "消息必须为 JSON"})
                continue
            if isinstance(msg, dict) and msg.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        notification_push.disconnect(user.id, websocket)
