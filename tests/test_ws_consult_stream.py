"""WS 流式法律咨询（B1）：`{"type": "consult"}` 边处理边推进度帧，最后推完整结果。

契约要点（docs/websocket-protocol.md）：
- 客户端消息 `{type: "consult", content, case_id?, contract_review_id?, draft_id?}`；
- 服务端事件 `consult_step`（volatile，不落库）逐帧推送，`consult_done`（非 volatile）带完整结果；
- 业务错误沿用稳定错误码（QUOTA_EXCEEDED / LEGAL_CONTEXT_CONFLICT / LEGAL_CONTEXT_NOT_FOUND …）。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.test_obs_api_p1 import BaseApiCase
from app.services.legal.legal_workspace_service import legal_workspace_module


class _FakeOperation:
    """`create_consultation` 的返回值最小形状（row + disclaimer）。"""

    def __init__(self, row_id: int = 77):
        self.row = SimpleNamespace(id=row_id, question="公司没提前通知就解除合同")
        self.disclaimer = {"level": "standard", "label": "AI 辅助结果，不构成正式法律意见"}


def _fake_create_factory(steps, *, error: Exception | None = None, captured: dict | None = None):
    async def _fake_create(db, user, question, *, case_id=None, contract_review_id=None,
                           draft_id=None, step_callback=None):
        if captured is not None:
            captured.update({
                "question": question, "case_id": case_id,
                "contract_review_id": contract_review_id, "draft_id": draft_id,
                "has_callback": step_callback is not None,
            })
        if error is not None:
            raise error
        for step in steps:
            await step_callback(step)
        return _FakeOperation()

    return _fake_create


def _drain(ws, limit: int = 10) -> list[dict]:
    events = []
    for _ in range(limit):
        event = ws.receive_json()
        events.append(event)
        if event["type"] in ("consult_done", "error"):
            break
    return events


class WsConsultStreamTests(BaseApiCase):
    STEPS = [
        {"id": "retrieve", "label": "检索法源", "status": "success",
         "duration_ms": 12, "detail": "带回 2 条候选法源"},
        {"id": "generate", "label": "生成建议", "status": "success",
         "duration_ms": 900, "detail": "模型返回结构化结果"},
    ]

    def _send(self, message: dict, *, steps=None, error=None, captured=None):
        fake = _fake_create_factory(steps if steps is not None else self.STEPS,
                                    error=error, captured=captured)
        with patch.object(legal_workspace_module, "create_consultation",
                          new=AsyncMock(side_effect=fake)), \
             patch("app.api.conversation.ws_api.serialize_workspace_row",
                   return_value={"id": 77, "question": "q"}):
            with self.client.websocket_connect(
                "/api/ws/chat", subprotocols=self._ws_subprotocols(), timeout=5,
            ) as ws:
                ws.send_json(message)
                welcome = ws.receive_json()
                self.assertEqual(welcome["type"], "welcome")
                return _drain(ws)

    def test_steps_stream_then_done_carries_result(self):
        events = self._send({"type": "consult", "content": "公司没提前通知就解除合同",
                             "case_id": 3})
        types = [e["type"] for e in events]
        self.assertEqual(types, ["consult_step", "consult_step", "consult_done"])

        first, second, done = events
        self.assertEqual(first["id"], "retrieve")
        self.assertEqual(first["label"], "检索法源")
        self.assertEqual(first["duration_ms"], 12)
        self.assertGreater(first["seq"], 0)
        self.assertEqual(second["id"], "generate")
        self.assertEqual(done["consultation"]["id"], 77)
        self.assertEqual(done["consultation"]["disclaimer_label"],
                         "AI 辅助结果，不构成正式法律意见")
        # 进度帧是 volatile（不落库），最终帧落库：seq 单调递增
        self.assertLess(first["seq"], done["seq"])

    def test_consult_passes_case_and_material_ids(self):
        captured: dict = {}
        self._send({"type": "consult", "content": "这份合同付款怎么约定",
                    "case_id": 3, "contract_review_id": 12},
                   captured=captured)
        self.assertEqual(captured["question"], "这份合同付款怎么约定")
        self.assertEqual(captured["case_id"], 3)
        self.assertEqual(captured["contract_review_id"], 12)
        self.assertIsNone(captured["draft_id"])
        self.assertTrue(captured["has_callback"], "必须把进度回调交给业务链路")

    def test_empty_content_rejected(self):
        events = self._send({"type": "consult", "content": "   "})
        self.assertEqual(events[0]["type"], "error")
        self.assertEqual(events[0]["code"], "WS_EMPTY_MESSAGE")

    def test_quota_exceeded_maps_to_error_frame(self):
        events = self._send({"type": "consult", "content": "问题"},
                            error=ValueError("QUOTA_EXCEEDED"))
        self.assertEqual(events[0]["type"], "error")
        self.assertEqual(events[0]["code"], "QUOTA_EXCEEDED")
        self.assertIn("配额", events[0]["message"])

    def test_context_conflict_maps_to_error_frame(self):
        events = self._send({"type": "consult", "content": "问题"},
                            error=ValueError("LEGAL_CONTEXT_CONFLICT"))
        self.assertEqual(events[0]["code"], "LEGAL_CONTEXT_CONFLICT")

    def test_context_not_found_maps_to_404_style_error(self):
        events = self._send({"type": "consult", "content": "问题"},
                            error=LookupError("LEGAL_CONTEXT_NOT_FOUND"))
        self.assertEqual(events[0]["code"], "LEGAL_CONTEXT_NOT_FOUND")
        self.assertIn("参考材料", events[0]["message"])

    def test_invalid_case_id_rejected_before_business_call(self):
        events = self._send({"type": "consult", "content": "问题", "case_id": "abc"})
        self.assertEqual(events[0]["code"], "WS_INVALID_PARAM")

    def test_legacy_chat_message_still_works(self):
        """旧客户端不带 type 的消息仍按 chat 处理（向后兼容）。"""
        async def _fake_stream(*_a, **_k):
            yield "好"

        with patch("app.api.conversation.ws_api.llm_client.chat_stream",
                   side_effect=_fake_stream):
            with self.client.websocket_connect(
                "/api/ws/chat", subprotocols=self._ws_subprotocols(), timeout=5,
            ) as ws:
                ws.send_json({"content": "你好"})
                ws.receive_json()  # welcome
                types = []
                for _ in range(6):
                    event = ws.receive_json()
                    types.append(event["type"])
                    if event["type"] == "done":
                        break
        self.assertIn("done", types)


if __name__ == "__main__":
    unittest.main()
