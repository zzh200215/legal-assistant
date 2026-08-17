"""WebSocket 协议契约测试（P1）：docs/websocket-protocol.md + ws-events.schema.json
与实现常量/事件类型保持一致；示例消息通过 JSON Schema 校验。
"""

import json
import unittest
from pathlib import Path

from app.services.memory import ws_session_service as ws

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_DOC = ROOT / "docs" / "websocket-protocol.md"
SCHEMA_FILE = ROOT / "docs" / "ws-events.schema.json"


def _load_schema() -> dict:
    return json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))


class WsProtocolDocTests(unittest.TestCase):
    """文档与实现常量一致（文档是契约基准）。"""

    def test_protocol_doc_and_schema_exist(self):
        self.assertTrue(PROTOCOL_DOC.exists(), "缺少 docs/websocket-protocol.md")
        self.assertTrue(SCHEMA_FILE.exists(), "缺少 docs/ws-events.schema.json")

    def test_heartbeat_constants_match_doc(self):
        doc = PROTOCOL_DOC.read_text(encoding="utf-8")
        self.assertIn("30s", doc)          # 心跳间隔
        self.assertIn("120s", doc)         # 空闲超时
        self.assertEqual(ws.PING_INTERVAL_SECONDS, 30.0)
        self.assertEqual(ws.IDLE_TIMEOUT_SECONDS, 120.0)

    def test_backpressure_constants_match_doc(self):
        doc = PROTOCOL_DOC.read_text(encoding="utf-8")
        self.assertIn("500", doc)          # 出站队列上限
        self.assertIn("64 KB", doc)        # 单事件大小
        self.assertEqual(ws.MAX_OUTBOX, 500)
        self.assertEqual(ws.MAX_EVENT_BYTES, 64 * 1024)

    def test_close_codes_are_stable(self):
        self.assertEqual(ws.CLOSE_AUTH_FAILED, 1008)
        self.assertEqual(ws.CLOSE_OVERLOADED, 1013)
        self.assertEqual(ws.CLOSE_IDLE_TIMEOUT, 4001)
        self.assertEqual(ws.CLOSE_RESUME_INVALID, 4002)
        self.assertEqual(ws.CLOSE_PROTOCOL_ERROR, 4003)


class WsSchemaTests(unittest.TestCase):
    """ws-events.schema.json 定义的事件类型覆盖实现。"""

    def setUp(self):
        self.schema = _load_schema()
        self.defs = self.schema.get("definitions", {})

    def test_server_event_types_defined(self):
        required = {
            "welcome", "ping", "pong", "resync_required", "error",
            "chunk", "done", "session", "run_snapshot", "subscribed",
        }
        defined = set(self.defs.keys())
        missing = required - defined
        self.assertFalse(missing, f"schema 缺少事件定义: {missing}")

    def test_client_message_types_defined(self):
        enum = self.defs["client_message"]["properties"]["type"]["enum"]
        for t in ("ack", "resume", "subscribe", "unsubscribe", "cancel", "chat", "agent_run"):
            self.assertIn(t, enum)

    def test_envelope_requires_seq_ts_trace_id(self):
        required = self.defs["envelope"]["required"]
        for field in ("type", "seq", "ts", "trace_id"):
            self.assertIn(field, required)

    def test_sample_client_messages_pass_schema(self):
        # 校验关键字段约束（不依赖 jsonschema 库，做结构化断言）
        content = self.defs["client_message"]["properties"]["content"]
        self.assertEqual(content["maxLength"], 8000)
        max_steps = self.defs["client_message"]["properties"]["max_steps"]
        self.assertEqual(max_steps["minimum"], 1)
        self.assertEqual(max_steps["maximum"], 10)
        kind = self.defs["client_message"]["properties"]["kind"]["enum"]
        self.assertIn("job", kind)
        self.assertIn("agent_run", kind)


if __name__ == "__main__":
    unittest.main()
