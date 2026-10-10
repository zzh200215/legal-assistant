"""站内通知 WS 推送测试：管理器连接清理、推送、no-op 兜底、端点握手/推送/断开。"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.notification import ws_notifications_api
from app.services.notification.notification_ws import NotificationPushManager, notification_push


class _FakeWS:
    """可 hash 的假 WebSocket（连接注册表用 set 存储）。"""

    def __init__(self):
        self.sent = []
        self.send_json = AsyncMock(side_effect=lambda payload: self.sent.append(payload))

    def __hash__(self):
        return id(self)


def _fake_ws():
    return _FakeWS()


class NotificationPushManagerTests(unittest.TestCase):
    def test_push_sends_to_all_and_keeps_healthy(self):
        manager = NotificationPushManager()
        ws1, ws2 = _fake_ws(), _fake_ws()
        manager.connect(7, ws1)
        manager.connect(7, ws2)
        asyncio.run(manager._push(7, {"type": "notification"}))
        self.assertEqual(len(ws1.sent), 1)
        self.assertEqual(len(ws2.sent), 1)
        self.assertEqual(manager.online_count(7), 2)

    def test_push_failure_disconnects_broken_socket(self):
        manager = NotificationPushManager()
        broken = _fake_ws()
        broken.send_json = AsyncMock(side_effect=RuntimeError("closed"))
        healthy = _fake_ws()
        manager.connect(7, broken)
        manager.connect(7, healthy)
        asyncio.run(manager._push(7, {"type": "notification"}))
        self.assertEqual(manager.online_count(7), 1)  # 坏连接被清理，好连接保留

    def test_notify_user_noop_without_loop_or_connections(self):
        manager = NotificationPushManager()
        # 无 loop、无连接：都不应抛出
        manager.notify_user(7, {"type": "notification"})
        manager.register_loop(asyncio.new_event_loop())
        manager.notify_user(7, {"type": "notification"})  # 有 loop 无连接：仍 no-op

    def test_disconnect_removes_socket_and_user(self):
        manager = NotificationPushManager()
        ws = _fake_ws()
        manager.connect(7, ws)
        manager.disconnect(7, ws)
        self.assertEqual(manager.online_count(7), 0)
        manager.disconnect(7, ws)  # 重复断开安全


class WsNotificationsEndpointTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        # 与 main.py 一致的挂载前缀
        app.include_router(ws_notifications_api.router, prefix="/api")
        self.client = TestClient(app)
        self.user = SimpleNamespace(id=7, organization_id=9, is_active=True)

    def test_handshake_ready_ping_and_push(self):
        with patch.object(ws_notifications_api, "_load_user", return_value=self.user), \
             self.client.websocket_connect(
                 "/api/ws/notifications", subprotocols=["bearer.fake-token"]
             ) as ws:
            ready = ws.receive_json()
            self.assertEqual(ready["type"], "ready")
            # 端点已把 TestClient portal loop 注册进管理器：此刻可实时推送
            notification_push.notify_user(7, {"type": "notification", "notification": {"id": 1, "title": "t"}})
            pushed = ws.receive_json()
            self.assertEqual(pushed["type"], "notification")
            self.assertEqual(pushed["notification"]["id"], 1)
            # ping/pong 心跳
            ws.send_text('{"type": "ping"}')
            self.assertEqual(ws.receive_json()["type"], "pong")
            self.assertEqual(notification_push.online_count(7), 1)
        # 连接关闭后管理器清理
        self.assertEqual(notification_push.online_count(7), 0)

    def test_auth_failure_sends_error_then_closes(self):
        """认证失败：accept 后发 error 帧再以 1008 关闭（客户端凭 onclose code 停止重连）。"""
        with patch.object(ws_notifications_api, "_load_user", return_value=None), \
             self.client.websocket_connect(
                 "/api/ws/notifications", subprotocols=["bearer.bad-token"]
             ) as ws:
            error = ws.receive_json()
            self.assertEqual(error["type"], "error")
            with self.assertRaises(WebSocketDisconnect):
                ws.receive_json()  # 随后连接关闭


if __name__ == "__main__":
    unittest.main()
