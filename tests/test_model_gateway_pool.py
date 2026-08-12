"""ModelGateway 连接池生命周期与旧接口兼容专项测试。"""

import unittest
from unittest.mock import AsyncMock, patch

from app.core.llm_client import LLMClient, ModelGateway, llm_client, model_gateway, settings
from app.core.ollama_client import OllamaClient, ollama_client


class ModelGatewayAliasTests(unittest.TestCase):
    def test_legacy_names_are_aliases_of_gateway(self):
        self.assertIs(LLMClient, ModelGateway)
        self.assertIs(llm_client, model_gateway)
        self.assertIs(OllamaClient, ModelGateway)
        self.assertIs(ollama_client, model_gateway)

    def test_legacy_alias_exposes_full_public_surface(self):
        for method in ("chat", "generate", "generate_with_images", "chat_stream", "embed"):
            self.assertTrue(callable(getattr(LLMClient, method)), f"缺少公开方法 {method}")


class ModelGatewayPoolTests(unittest.IsolatedAsyncioTestCase):
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
            patch.object(settings, "LLM_SIMPLE_REQUEST_MAX_CHARS", 600),
            patch.object(settings, "LLM_PRIMARY_REQUEST_RETRIES", 1),
            patch.object(settings, "LLM_FALLBACK_REQUEST_RETRIES", 1),
            patch.object(settings, "LLM_REQUEST_TIMEOUT_SECONDS", 60),
        ]
        for item in self.setting_patches:
            item.start()
        self.gateway = ModelGateway()

    def tearDown(self):
        for item in reversed(self.setting_patches):
            item.stop()

    async def test_same_target_reuses_same_pooled_client(self):
        first = self.gateway._get_client(self.gateway.primary_target, timeout=60)
        second = self.gateway._get_client(self.gateway.primary_target, timeout=60)
        self.assertIs(first, second)
        await self.gateway.close()

    async def test_pool_isolates_clients_by_supplier_target(self):
        primary = self.gateway._get_client(self.gateway.primary_target, timeout=60)
        small = self.gateway._get_client(self.gateway.small_target, timeout=60)
        self.assertIsNot(primary, small)
        await self.gateway.close()

    async def test_close_closes_each_client_and_empties_pool(self):
        closed = []

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def aclose(self):
                closed.append(True)

        with patch("app.core.llm_client.httpx.AsyncClient", FakeClient):
            self.gateway._get_client(self.gateway.primary_target, timeout=60)
            self.gateway._get_client(self.gateway.small_target, timeout=60)

        self.assertEqual(len(self.gateway._clients), 2)
        await self.gateway.close()
        self.assertEqual(self.gateway._clients, {})
        self.assertEqual(len(closed), 2)
        # close 幂等：重复调用不抛异常、池保持为空
        await self.gateway.close()
        self.assertEqual(self.gateway._clients, {})

    async def test_close_tolerates_clients_without_aclose(self):
        class FakeClientNoClose:
            def __init__(self, *args, **kwargs):
                pass

        with patch("app.core.llm_client.httpx.AsyncClient", FakeClientNoClose):
            self.gateway._get_client(self.gateway.primary_target, timeout=60)
        await self.gateway.close()  # 不应抛异常
        self.assertEqual(self.gateway._clients, {})

    async def test_shutdown_is_close(self):
        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def aclose(self):
                pass

        with patch("app.core.llm_client.httpx.AsyncClient", FakeClient):
            self.gateway._get_client(self.gateway.primary_target, timeout=60)
        await self.gateway.shutdown()
        self.assertEqual(self.gateway._clients, {})

    async def test_start_precreates_primary_and_small_clients(self):
        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def aclose(self):
                pass

        with patch("app.core.llm_client.httpx.AsyncClient", FakeClient):
            self.gateway.start()
        self.assertTrue(self.gateway._started)
        self.assertEqual(len(self.gateway._clients), 2)  # primary + small
        await self.gateway.close()
        self.assertFalse(self.gateway._started)

    async def test_chat_reuses_single_pooled_client_across_requests(self):
        constructed = []

        class FakeClient:
            def __init__(self, *args, **kwargs):
                constructed.append(self)

            async def aclose(self):
                pass

        fake_response = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        }
        with patch("app.core.llm_client.httpx.AsyncClient", FakeClient), patch.object(
            self.gateway, "_post_json_with_retry", new=AsyncMock(return_value=fake_response),
        ), patch.object(self.gateway, "_record_usage"):
            await self.gateway.chat([{"role": "user", "content": "你好"}], action="chat")
            await self.gateway.chat([{"role": "user", "content": "再问一次"}], action="chat")

        self.assertEqual(len(constructed), 1)
        await self.gateway.close()

    async def test_stream_uses_pooled_client_and_closes_stream(self):
        closed_streams = []
        constructed = []

        class FakeStream:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                closed_streams.append(True)
                return False

            async def aiter_lines(self):
                yield 'data: {"choices":[{"delta":{"content":"hi"},"finish_reason":null}]}'
                yield "data: [DONE]"

            def raise_for_status(self):
                return None

        class FakeClient:
            def __init__(self, *args, **kwargs):
                constructed.append(self)

            async def aclose(self):
                pass

            def stream(self, method, url, json=None, headers=None):
                return FakeStream()

        with patch("app.core.llm_client.httpx.AsyncClient", FakeClient), patch.object(
            self.gateway, "_record_usage",
        ):
            chunks = [
                chunk async for chunk in self.gateway.chat_stream(
                    [{"role": "user", "content": "请分析合同付款责任"}],
                    action="chat_stream",
                )
            ]

        self.assertEqual(chunks, ["hi"])
        self.assertEqual(len(constructed), 1)
        self.assertEqual(len(closed_streams), 1)
        await self.gateway.close()

    async def test_embed_reuses_pooled_client_across_calls(self):
        constructed = []

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "data": [{"embedding": [1.0, 2.0]}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 0},
                }

        class FakeClient:
            def __init__(self, *args, **kwargs):
                constructed.append(self)

            async def aclose(self):
                pass

            async def post(self, url, json=None, headers=None):
                return FakeResponse()

        with patch("app.core.llm_client.httpx.AsyncClient", FakeClient), patch.object(
            self.gateway, "_record_usage",
        ):
            await self.gateway.embed(["第一段"])
            await self.gateway.embed(["第二段"])

        self.assertEqual(len(constructed), 1)
        await self.gateway.close()

    async def test_generate_with_images_reuses_pooled_client(self):
        constructed = []

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "choices": [{"message": {"content": "描述"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                }

        class FakeClient:
            def __init__(self, *args, **kwargs):
                constructed.append(self)

            async def aclose(self):
                pass

            async def post(self, url, json=None, headers=None):
                return FakeResponse()

        with patch("app.core.llm_client.httpx.AsyncClient", FakeClient), patch.object(
            self.gateway, "_record_usage",
        ):
            await self.gateway.generate_with_images("描述图片", ["https://example.com/a.png"])
            await self.gateway.generate_with_images("再看一张", ["https://example.com/b.png"])

        self.assertEqual(len(constructed), 1)
        await self.gateway.close()


if __name__ == "__main__":
    unittest.main()
