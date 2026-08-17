"""P0 出站数据保护网关测试：分级 / PII 脱敏 / 极敏感拦截 / 检测故障 fail-closed / 审计脱敏。

覆盖：
- 未命中 PII 的正常请求行为不变（原样放行）。
- 命中 PII：发往供应商的是脱敏文本，审计字段只含规则 code，不含原始 PII。
- highly_sensitive 默认拦截；仅显式受控放行名单可放行（且仍脱敏）。
- 检测服务异常：默认 fail closed 阻断全部出站并记录原因；warn 为逃生通道。
- 网关集成：generate/chat/embed 实际载荷不含原始 PII；拦截抛出稳定业务错误。
- 新增地址规则（cn_address）可检测并脱敏。
"""
import json
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.config import get_settings
from app.core.database import Base
from app.core.llm_client import LLMClient
from app.core.data_levels import DataLevel
from app.services.llm.llm_outbound_gate import BLOCK_CODE, outbound_gate


def _make_engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


class OutboundGateUnitTests(unittest.TestCase):
    """纯网关判定与变换（无 DB/IO）。"""

    def test_no_pii_request_unchanged_and_internal_level(self):
        pieces = ["请总结这份合同的核心条款"]
        safe, result = outbound_gate.guard(pieces=pieces, action="rag_answer")
        self.assertEqual(safe, pieces)
        self.assertFalse(result.blocked)
        self.assertIs(result.data_level, DataLevel.INTERNAL)
        self.assertEqual(result.pii_hit_count, 0)

    def test_legal_action_base_level_is_sensitive(self):
        _, result = outbound_gate.guard(pieces=["无敏感内容"], action="legal_consultation")
        self.assertIs(result.data_level, DataLevel.SENSITIVE)
        self.assertFalse(result.blocked)

    def test_phone_redacted_before_send(self):
        pieces = ["请联系 13812345678 获取合同"]
        safe, result = outbound_gate.guard(pieces=pieces, action="legal_consultation")
        self.assertIn("mobile_phone", result.pii_hit_codes)
        self.assertGreater(result.pii_hit_count, 0)
        self.assertGreater(result.redacted_count, 0)
        self.assertNotIn("13812345678", safe[0])
        self.assertIn("138****5678", safe[0])
        self.assertIs(result.data_level, DataLevel.SENSITIVE)

    def test_id_card_escalates_to_highly_sensitive_and_blocks_by_default(self):
        pieces = ["身份证号 11010519491231002X，请分析"]
        _, result = outbound_gate.guard(pieces=pieces, action="legal_consultation")
        self.assertIs(result.data_level, DataLevel.HIGHLY_SENSITIVE)
        self.assertTrue(result.blocked)
        self.assertIn("highly_sensitive_not_allowed", result.blocked_reason or "")

    def test_allowlisted_action_passes_but_still_redacts(self):
        settings = get_settings()
        with patch.object(
            settings,
            "LLM_OUTBOUND_HIGHLY_SENSITIVE_ACTIONS_JSON",
            '["legal_consultation"]',
        ):
            pieces = ["身份证号 11010519491231002X，请分析"]
            safe, result = outbound_gate.guard(pieces=pieces, action="legal_consultation")
        self.assertFalse(result.blocked)
        self.assertIs(result.data_level, DataLevel.HIGHLY_SENSITIVE)
        self.assertNotIn("11010519491231002X", safe[0])

    def test_unknown_action_deny_by_default_blocks_highly_sensitive_content(self):
        _, result = outbound_gate.guard(
            pieces=["令牌 sk_abcdefghijklmnopqrstuvwxyz123456"],
            action="brand_new_action",
        )
        self.assertTrue(result.blocked)
        self.assertIn("api_token", result.pii_hit_codes)

    def test_detection_failure_fails_closed_by_default(self):
        with patch(
            "app.services.org.data_protection_service.data_protection_service.inspect",
            side_effect=RuntimeError("boom"),
        ):
            _, result = outbound_gate.guard(pieces=["任何内容"], action="chat")
        self.assertTrue(result.blocked)
        self.assertTrue(result.detector_error)
        self.assertEqual(result.blocked_reason, "dlp_detection_failed")

    def test_detection_failure_warn_is_escape_hatch(self):
        settings = get_settings()
        with patch(
            "app.services.org.data_protection_service.data_protection_service.inspect",
            side_effect=RuntimeError("boom"),
        ), patch.object(settings, "LLM_OUTBOUND_DLP_FAILURE_ACTION", "warn"):
            _, result = outbound_gate.guard(pieces=["任何内容"], action="chat")
        self.assertFalse(result.blocked)
        self.assertTrue(result.detector_error)

    def test_dlp_disabled_passes_through_unchanged(self):
        settings = get_settings()
        with patch.object(settings, "LLM_OUTBOUND_DLP_ENABLED", False):
            pieces = ["手机号 13812345678"]
            safe, result = outbound_gate.guard(pieces=pieces, action="chat")
        self.assertEqual(safe, pieces)
        self.assertFalse(result.blocked)
        self.assertEqual(result.pii_hit_count, 0)

    def test_action_level_override_to_highly_sensitive_blocks_without_pii(self):
        settings = get_settings()
        with patch.object(
            settings,
            "LLM_OUTBOUND_ACTION_DATA_LEVEL_JSON",
            '{"legal_contract_review": "highly_sensitive"}',
        ):
            _, result = outbound_gate.guard(pieces=["普通合同文本"], action="legal_contract_review")
        self.assertTrue(result.blocked)
        self.assertIs(result.data_level, DataLevel.HIGHLY_SENSITIVE)
        self.assertEqual(result.pii_hit_count, 0)

    def test_audit_fields_never_contain_raw_pii(self):
        pieces = ["邮箱 test@example.com 与手机 13812345678"]
        _, result = outbound_gate.guard(pieces=pieces, action="chat")
        joined = " ".join(result.pii_hit_codes) + str(result.pii_hit_count) + str(result.redacted_count)
        self.assertNotIn("13812345678", joined)
        self.assertNotIn("test@example.com", joined)
        self.assertIn("mobile_phone", result.pii_hit_codes)
        self.assertIn("email_address", result.pii_hit_codes)

    def test_cn_address_rule_detects_and_redacts(self):
        pieces = ["送达地址：上海市浦东新区世纪大道100号"]
        safe, result = outbound_gate.guard(pieces=pieces, action="chat")
        self.assertIn("cn_address", result.pii_hit_codes)
        self.assertNotIn("世纪大道100号", safe[0])

    def test_multiple_message_pieces_transform_in_order(self):
        pieces = ["第一段", "电话 13812345678", "第三段"]
        safe, result = outbound_gate.guard(pieces=pieces, action="chat")
        self.assertEqual(len(safe), 3)
        self.assertEqual(safe[0], "第一段")
        self.assertNotIn("13812345678", safe[1])
        self.assertEqual(safe[2], "第三段")
        self.assertEqual(result.redacted_count, 1)


class OutboundGateGatewayTests(unittest.IsolatedAsyncioTestCase):
    """网关集成：ModelGateway 六个入口实际载荷不含原始 PII。"""

    def setUp(self):
        self.client = LLMClient()

    async def test_generate_redacts_pii_in_outgoing_request(self):
        captured = {}

        async def fake_routing(*, source_text, request):
            captured["request"] = request
            return "ok"

        with patch.object(self.client, "_request_text_with_routing", new=AsyncMock(side_effect=fake_routing)):
            result = await self.client.generate(
                "电话 13812345678，请分析", action="legal_consultation", user_id=None,
            )
        self.assertEqual(result, "ok")
        self.assertNotIn("13812345678", captured["request"].prompt)
        self.assertIn("138****5678", captured["request"].prompt)
        self.assertEqual(captured["request"].data_level, "sensitive")
        self.assertEqual(captured["request"].pii_hit_count, 1)

    async def test_chat_blocks_highly_sensitive_and_raises_stable_error(self):
        settings = get_settings()
        with patch.object(settings, "LLM_OUTBOUND_AUDIT_ENABLED", False):
            with self.assertRaises(Exception) as ctx:
                await self.client.chat(
                    [{"role": "user", "content": "身份证 11010519491231002X"}],
                    action="legal_consultation",
                    user_id=None,
                )
        error = ctx.exception
        self.assertEqual(error.status_code, 403)
        self.assertEqual(error.code, BLOCK_CODE)
        payload = error.detail
        self.assertEqual(payload["code"], BLOCK_CODE)
        inner = payload.get("detail", {})
        self.assertIn("highly_sensitive_not_allowed", inner.get("reason", ""))
        self.assertEqual(inner.get("data_level"), "highly_sensitive")

    async def test_chat_benign_request_unchanged(self):
        captured = {}

        async def fake_routing(*, source_text, request):
            captured["request"] = request
            return "ok"

        with patch.object(self.client, "_request_text_with_routing", new=AsyncMock(side_effect=fake_routing)):
            result = await self.client.chat(
                [{"role": "user", "content": "你好，帮我看看合同"}],
                action="chat",
                user_id=None,
            )
        self.assertEqual(result, "ok")
        self.assertEqual(captured["request"].messages[0]["content"], "你好，帮我看看合同")
        self.assertEqual(captured["request"].data_level, "internal")

    async def test_embed_redacts_texts_in_payload(self):
        captured = {}

        async def fake_post(client, *, url, payload, headers, retries=1):
            captured["payload"] = payload
            return {"data": [{"embedding": [0.1, 0.2]}], "usage": {"prompt_tokens": 1, "completion_tokens": 0}}

        with patch.object(self.client, "_post_json_with_retry", new=AsyncMock(side_effect=fake_post)):
            embeddings = await self.client.embed(
                ["联系 13812345678 签署合同"], action="embedding", user_id=None,
            )
        self.assertEqual(len(embeddings), 1)
        payload_input = captured["payload"]["input"]
        self.assertNotIn("13812345678", payload_input[0])


class OutboundGateAuditTests(unittest.TestCase):
    """拦截审计落库：字段齐全且不含原始 PII。"""

    def test_blocked_audit_row_carries_metadata_only(self):
        from app.models.llm_call_log import LLMCallLog

        engine = _make_engine()
        Base.metadata.create_all(bind=engine)
        session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        db = session()
        # addCleanup 按 LIFO 执行：先关 session，再 dispose engine（避免在已关闭连接上 rollback）。
        self.addCleanup(engine.dispose)
        self.addCleanup(db.close)

        client = LLMClient()
        settings = get_settings()
        with patch(
            "app.services.llm.llm_observability_service.SessionLocal",
            session,
        ):
            with self.assertRaises(Exception):
                client._apply_outbound_gate(
                    pieces=["身份证 11010519491231002X 请分析"],
                    action="legal_consultation",
                    user_id=7,
                    request_id="req1234567890",
                    model_name="qwen-plus",
                )
        row = db.query(LLMCallLog).one()
        self.assertEqual(row.status, "blocked")
        self.assertEqual(row.action, "legal_consultation")
        self.assertEqual(row.user_id, 7)
        self.assertEqual(row.data_level, "highly_sensitive")
        self.assertEqual(row.provider, settings.LLM_PROVIDER)
        self.assertIn("highly_sensitive_not_allowed", row.blocked_reason or "")
        codes = json.loads(row.pii_hit_codes or "[]")
        # 17 位数字身份证串同时命中 bank_card 与 cn_id_card（检测器既有行为），
        # 两者都是规则 code，绝无原始 PII。
        self.assertEqual(codes, ["bank_card", "cn_id_card"])
        excerpt = row.request_excerpt or ""
        self.assertNotIn("11010519491231002X", excerpt)
        self.assertNotIn("11010519491231002X", row.error_message or "")


if __name__ == "__main__":
    unittest.main()
