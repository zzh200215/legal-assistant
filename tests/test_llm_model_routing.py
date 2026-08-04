import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.core.llm_client import LLMClient, settings


class LLMModelRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.setting_patches = [
            patch.object(settings, "LLM_PROVIDER", "openai_compatible"),
            patch.object(settings, "LLM_API_BASE_URL", "https://primary.example/v1"),
            patch.object(settings, "LLM_API_KEY", "primary-key"),
            patch.object(settings, "LLM_MODEL", "qwen-plus"),
            patch.object(settings, "LLM_MODEL_ROUTING_ENABLED", True),
            patch.object(settings, "LLM_SMALL_MODEL", "qwen-turbo"),
            patch.object(settings, "LLM_SMALL_MODEL_PROVIDER", "openai_compatible"),
            patch.object(settings, "LLM_SMALL_MODEL_API_BASE_URL", "https://small.example/v1"),
            patch.object(settings, "LLM_SMALL_MODEL_API_KEY", "small-key"),
            patch.object(settings, "LLM_SIMPLE_REQUEST_MAX_CHARS", 80),
            patch.object(settings, "LLM_PRIMARY_REQUEST_RETRIES", 1),
            patch.object(settings, "LLM_FALLBACK_REQUEST_RETRIES", 1),
            patch.object(settings, "LLM_MODEL_FALLBACK_ENABLED", True),
            patch.object(settings, "LLM_SMALL_MODEL_FALLBACK_TO_PRIMARY", True),
        ]
        for item in self.setting_patches:
            item.start()
        self.client = LLMClient()

    def tearDown(self):
        for item in reversed(self.setting_patches):
            item.stop()

    def test_short_general_request_uses_small_qwen_model(self):
        target = self.client._select_text_target("把这句话改得更简洁", "chat")

        self.assertEqual(target.role, "small")
        self.assertEqual(target.model, "qwen-turbo")
        self.assertEqual(target.base_url, "https://small.example/v1")

    def test_legal_or_long_request_uses_primary_model(self):
        legal_target = self.client._select_text_target("请审查这份合同的违约责任", "chat")
        long_target = self.client._select_text_target("a" * 81, "chat")
        action_target = self.client._select_text_target("简短任务", "legal_consultation")

        self.assertEqual(legal_target.role, "primary")
        self.assertEqual(long_target.role, "primary")
        self.assertEqual(action_target.role, "primary")

    def test_small_target_uses_its_own_endpoint_key_and_model(self):
        payload = self.client._build_generate_payload("帮我润色标题", 0.2, target=self.client.small_target)
        headers = self.client._build_headers(self.client.small_target)

        self.assertEqual(payload["model"], "qwen-turbo")
        self.assertEqual(self.client._generate_url(self.client.small_target), "https://small.example/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer small-key")

    async def test_primary_provider_failure_degrades_to_small_model(self):
        calls = []

        async def fake_request(*, target, **kwargs):
            calls.append(target.role)
            if target.role == "primary":
                raise httpx.ReadTimeout("primary timeout")
            return "small model response"

        with patch.object(self.client, "_request_text_once", new=AsyncMock(side_effect=fake_request)):
            result = await self.client.generate("请审查合同付款条款", action="legal_consultation")

        self.assertEqual(result, "small model response")
        self.assertEqual(calls, ["primary", "small"])

    async def test_small_model_failure_can_recover_with_primary_model(self):
        calls = []

        async def fake_request(*, target, **kwargs):
            calls.append(target.role)
            if target.role == "small":
                raise httpx.ConnectError("small unavailable")
            return "primary model response"

        with patch.object(self.client, "_request_text_once", new=AsyncMock(side_effect=fake_request)):
            result = await self.client.generate("帮我润色一个标题", action="email_polish")

        self.assertEqual(result, "primary model response")
        self.assertEqual(calls, ["small", "primary"])

    async def test_non_provider_error_does_not_bypass_to_another_model(self):
        calls = []

        async def fake_request(*, target, **kwargs):
            calls.append(target.role)
            raise ValueError("invalid request")

        with patch.object(self.client, "_request_text_once", new=AsyncMock(side_effect=fake_request)):
            with self.assertRaisesRegex(ValueError, "invalid request"):
                await self.client.generate("短文本", action="email_polish")

        self.assertEqual(calls, ["small"])

    async def test_stream_degrades_before_first_chunk_when_primary_times_out(self):
        requested_models = []

        class FakeStream:
            def __init__(self, model):
                self.model = model

            async def __aenter__(self):
                if self.model == "qwen-plus":
                    raise httpx.ReadTimeout("primary timeout")
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def aiter_lines(self):
                yield 'data: {"choices":[{"delta":{"content":"备用回答"},"finish_reason":null}]}'
                yield "data: [DONE]"

            def raise_for_status(self):
                return None

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def stream(self, method, url, json=None, headers=None):
                requested_models.append(json["model"])
                return FakeStream(json["model"])

        with patch("app.core.llm_client.httpx.AsyncClient", FakeClient), patch.object(self.client, "_record_usage"):
            chunks = [
                chunk async for chunk in self.client.chat_stream(
                    [{"role": "user", "content": "请分析合同付款责任"}],
                    action="chat_stream",
                )
            ]

        self.assertEqual(chunks, ["备用回答"])
        self.assertEqual(requested_models, ["qwen-plus", "qwen-turbo"])


if __name__ == "__main__":
    unittest.main()
