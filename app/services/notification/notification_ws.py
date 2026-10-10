"""站内通知 WebSocket 实时推送管理器（ux-audit M-9 中期项）。

设计：
1. 进程内注册表：user_id -> 在线 WebSocket 集合，/ws/notifications 端点维护；
2. notify_user 是同步线程安全入口——notification_service（线程池/任意线程）可直接调用，
   内部用 run_coroutine_threadsafe 把推送投递回 uvicorn 主 loop；
3. loop 感知：只有本进程的 WS 端点 accept 时会注册 loop；Celery worker 里没有
   loop，notify_user 自动 no-op（此类场景由 30s 轮询兜底）；
4. 推送失败（客户端已断开）只清理连接不抛出，绝不影响通知创建主流程。
"""
import asyncio
import logging

logger = logging.getLogger(__name__)


class NotificationPushManager:
    def __init__(self) -> None:
        self._connections: dict[int, set] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def register_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """WS 端点 accept 后注册本进程事件循环（幂等）。"""
        self._loop = loop

    def connect(self, user_id: int, websocket) -> None:
        self._connections.setdefault(user_id, set()).add(websocket)

    def disconnect(self, user_id: int, websocket) -> None:
        conns = self._connections.get(user_id)
        if not conns:
            return
        conns.discard(websocket)
        if not conns:
            self._connections.pop(user_id, None)

    def online_count(self, user_id: int) -> int:
        return len(self._connections.get(user_id, ()))

    async def _push(self, user_id: int, payload: dict) -> None:
        for websocket in list(self._connections.get(user_id, ())):
            try:
                await websocket.send_json(payload)
            except Exception:  # noqa: BLE001 — 断开的连接直接清理
                self.disconnect(user_id, websocket)

    def notify_user(self, user_id: int, payload: dict) -> None:
        """同步入口：向该用户的全部在线连接推送。无连接/无 loop 时静默跳过。"""
        if self._loop is None or not self._connections.get(user_id):
            return
        try:
            asyncio.run_coroutine_threadsafe(self._push(user_id, payload), self._loop)
        except Exception:  # noqa: BLE001 — loop 已关闭等极端场景
            logger.warning("[notification-ws] push to user %s failed", user_id, exc_info=True)


notification_push = NotificationPushManager()
