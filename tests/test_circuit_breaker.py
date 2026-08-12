"""供应商健康 / 熔断 / 半开恢复 专项测试。"""

import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.core.circuit_breaker import (
    CircuitBreaker,
    InMemoryCircuitBackend,
    RedisCircuitBackend,
    _NoopCircuitBreaker,
    build_circuit_breaker,
    counts_toward_breaker,
)
from app.core.llm_client import ModelGateway, settings
from app.core.model_policy import ModelError, ModelErrorKind, get_task_policy


def _http_error(status: int, body: str = "{}") -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    response = httpx.Response(status, request=request, text=body)
    return httpx.HTTPStatusError(f"HTTP {status}", request=request, response=response)


class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value):
        self.store[key] = value

    def delete(self, key):
        self.store.pop(key, None)

    def scan_iter(self, pattern):
        return list(self.store.keys())


class CircuitBreakerUnitTests(unittest.TestCase):
    def test_counts_toward_breaker_kinds(self):
        for kind in (ModelErrorKind.TIMEOUT, ModelErrorKind.TRANSPORT, ModelErrorKind.PROVIDER_5XX):
            self.assertTrue(counts_toward_breaker(kind), kind)
        for kind in (
            ModelErrorKind.VALIDATION,
            ModelErrorKind.AUTHENTICATION,
            ModelErrorKind.PERMISSION,
            ModelErrorKind.CONTENT_BLOCKED,
            ModelErrorKind.RATE_LIMITED,
            ModelErrorKind.INVALID_RESPONSE,
            ModelErrorKind.CIRCUIT_OPEN,
        ):
            self.assertFalse(counts_toward_breaker(kind), kind)

    def test_opens_after_threshold_consecutive_failures(self):
        breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=30)
        key = "p|u|chat"
        breaker.record_failure(key, counts=True)
        self.assertEqual(breaker.state(key), "closed")
        breaker.record_failure(key, counts=True)
        self.assertEqual(breaker.state(key), "open")
        self.assertFalse(breaker.can_attempt(key))

    def test_non_counting_failures_do_not_open(self):
        breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=30)
        key = "p|u|chat"
        breaker.record_failure(key, counts=False)  # 参数/鉴权等
        breaker.record_failure(key, counts=False)
        self.assertEqual(breaker.state(key), "closed")

    def test_open_skips_attempts(self):
        breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=30)
        key = "p|u|chat"
        breaker.record_failure(key, counts=True)
        self.assertFalse(breaker.can_attempt(key))

    def test_cooling_then_half_open(self):
        now = [100.0]

        def fake_now():
            return now[0]

        breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=30, now=fake_now)
        key = "p|u|chat"
        breaker.record_failure(key, counts=True)  # opened_at=100
        self.assertEqual(breaker.state(key), "open")
        self.assertFalse(breaker.can_attempt(key))  # 冷却中
        now[0] = 131.0
        self.assertTrue(breaker.can_attempt(key))  # 冷却结束 → half_open
        self.assertEqual(breaker.state(key), "half_open")

    def test_half_open_probe_success_closes(self):
        now = [100.0]
        breaker = CircuitBreaker(
            failure_threshold=1, cooldown_seconds=0, half_open_max_concurrency=1, now=lambda: now[0],
        )
        key = "p|u|chat"
        breaker.record_failure(key, counts=True)  # open
        self.assertTrue(breaker.can_attempt(key))  # → half_open
        breaker.record_success(key)
        self.assertEqual(breaker.state(key), "closed")
        self.assertTrue(breaker.can_attempt(key))

    def test_half_open_probe_failure_reopens(self):
        now = [100.0]

        def fake_now():
            return now[0]

        breaker = CircuitBreaker(
            failure_threshold=1, cooldown_seconds=30, half_open_max_concurrency=1, now=fake_now,
        )
        key = "p|u|chat"
        breaker.record_failure(key, counts=True)  # open @100
        now[0] = 131.0
        self.assertTrue(breaker.can_attempt(key))  # 冷却结束 → half_open
        breaker.record_failure(key, counts=True)  # 探测失败 → 重开 @131
        self.assertEqual(breaker.state(key), "open")
        self.assertFalse(breaker.can_attempt(key))  # 仍处冷却窗口

    def test_half_open_probe_4xx_closes(self):
        now = [100.0]
        breaker = CircuitBreaker(
            failure_threshold=1, cooldown_seconds=0, half_open_max_concurrency=1, now=lambda: now[0],
        )
        key = "p|u|chat"
        breaker.record_failure(key, counts=True)  # open
        self.assertTrue(breaker.can_attempt(key))  # → half_open
        breaker.record_failure(key, counts=False)  # 4xx 响应 → 供应商在线 → 关闭
        self.assertEqual(breaker.state(key), "closed")

    def test_half_open_probe_concurrency_limit(self):
        now = [100.0]
        breaker = CircuitBreaker(
            failure_threshold=1, cooldown_seconds=0, half_open_max_concurrency=1, now=lambda: now[0],
        )
        key = "p|u|chat"
        breaker.record_failure(key, counts=True)  # open
        self.assertTrue(breaker.can_attempt(key))  # 占用唯一探测额度
        self.assertFalse(breaker.can_attempt(key))

    def test_capabilities_are_isolated(self):
        breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=30)
        chat_key = breaker.key(provider="p", base_url="u", task="chat")
        embed_key = breaker.key(provider="p", base_url="u", task="embedding")
        breaker.record_failure(chat_key, counts=True)  # chat 熔断
        self.assertEqual(breaker.state(chat_key), "open")
        self.assertEqual(breaker.state(embed_key), "closed")
        self.assertTrue(breaker.can_attempt(embed_key))

    def test_key_composition(self):
        breaker = CircuitBreaker()
        self.assertNotEqual(
            breaker.key(provider="p", base_url="u", task="chat"),
            breaker.key(provider="p", base_url="u", task="embedding"),
        )
        self.assertNotEqual(
            breaker.key(provider="p", base_url="u", task="chat"),
            breaker.key(provider="p", base_url="u2", task="chat"),
        )

    def test_reset_clears_state(self):
        breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=30)
        key = "p|u|chat"
        breaker.record_failure(key, counts=True)
        self.assertEqual(breaker.state(key), "open")
        breaker.reset()
        self.assertEqual(breaker.state(key), "closed")

    def test_redis_backend_persists_state(self):
        client = FakeRedis()
        backend = RedisCircuitBackend(client, prefix="test:cb")
        breaker = CircuitBreaker(backend=backend, failure_threshold=1, cooldown_seconds=30)
        key = "p|u|chat"
        breaker.record_failure(key, counts=True)
        self.assertEqual(breaker.state(key), "open")
        # 新实例同一 backend 能读到状态
        breaker2 = CircuitBreaker(backend=backend, failure_threshold=1, cooldown_seconds=30)
        self.assertEqual(breaker2.state(key), "open")
        breaker2.reset()
        self.assertEqual(breaker.state(key), "closed")

    def test_disabled_builder_returns_noop(self):
        with patch.object(settings, "CIRCUIT_BREAKER_ENABLED", False):
            breaker = build_circuit_breaker()
        self.assertIsInstance(breaker, _NoopCircuitBreaker)
        key = breaker.key(provider="p", base_url="u", task="chat")
        self.assertTrue(breaker.can_attempt(key))
        breaker.record_failure(key, counts=True)
        self.assertEqual(breaker.state(key), "closed")


class _GatewaySettingsMixin:
    def _start_gateway_settings(self):
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
            patch.object(settings, "LLM_MODEL_FALLBACK_ENABLED", True),
            patch.object(settings, "LLM_SMALL_MODEL_FALLBACK_TO_PRIMARY", True),
        ]
        for item in self.setting_patches:
            item.start()
        self.gateway = ModelGateway()
        # 集成测试用可控熔断参数替换实例默认熔断器
        self.gateway.circuit_breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=3600)

    def _stop_gateway_settings(self):
        for item in reversed(self.setting_patches):
            item.stop()


class ModelGatewayCircuitTests(_GatewaySettingsMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._start_gateway_settings()

    def tearDown(self):
        self._stop_gateway_settings()

    async def test_candidate_targets_skips_open_target(self):
        primary_key = self.gateway._circuit_key(self.gateway.primary_target, "chat")
        self.gateway.circuit_breaker.record_failure(primary_key, counts=True)
        targets = self.gateway._candidate_targets("请审查合同付款条款", "legal_consultation")
        self.assertEqual([target.role for target in targets], ["small"])

    async def test_fallback_uses_available_target_when_primary_open(self):
        primary_key = self.gateway._circuit_key(self.gateway.primary_target, "chat")
        self.gateway.circuit_breaker.record_failure(primary_key, counts=True)
        calls = []

        async def fake_request(*, target, **kwargs):
            calls.append(target.role)
            return "fallback ok"

        with patch.object(self.gateway, "_request_text_once", new=AsyncMock(side_effect=fake_request)):
            result = await self.gateway.generate("请审查合同付款条款", action="legal_consultation")

        self.assertEqual(result, "fallback ok")
        self.assertEqual(calls, ["small"])

    async def test_routing_raises_circuit_open_when_no_target_available(self):
        primary_key = self.gateway._circuit_key(self.gateway.primary_target, "chat")
        small_key = self.gateway._circuit_key(self.gateway.small_target, "chat")
        self.gateway.circuit_breaker.record_failure(primary_key, counts=True)
        self.gateway.circuit_breaker.record_failure(small_key, counts=True)
        with self.assertRaises(ModelError) as ctx:
            await self.gateway.generate("请审查合同付款条款", action="legal_consultation")
        self.assertEqual(ctx.exception.kind, ModelErrorKind.CIRCUIT_OPEN)

    async def test_embed_raises_circuit_open_when_primary_open(self):
        embedding_key = self.gateway._circuit_key(self.gateway.primary_target, "embedding")
        self.gateway.circuit_breaker.record_failure(embedding_key, counts=True)
        with self.assertRaises(ModelError) as ctx:
            await self.gateway.embed(["第一段"])
        self.assertEqual(ctx.exception.kind, ModelErrorKind.CIRCUIT_OPEN)

    async def test_vision_raises_circuit_open_when_primary_open(self):
        vision_key = self.gateway._circuit_key(self.gateway.primary_target, "vision")
        self.gateway.circuit_breaker.record_failure(vision_key, counts=True)
        with self.assertRaises(ModelError) as ctx:
            await self.gateway.generate_with_images("描述图片", ["https://example.com/a.png"])
        self.assertEqual(ctx.exception.kind, ModelErrorKind.CIRCUIT_OPEN)

    async def test_request_failure_updates_breaker_state(self):
        async def fail_post(client, *, url, payload, headers, retries=3):
            raise _http_error(500)

        with patch.object(self.gateway, "_post_json_with_retry", new=AsyncMock(side_effect=fail_post)):
            with self.assertRaises(httpx.HTTPStatusError):
                await self.gateway.generate("请审查合同付款条款", action="legal_consultation")

        primary_key = self.gateway._circuit_key(self.gateway.primary_target, "chat")
        self.assertEqual(self.gateway.circuit_breaker.state(primary_key), "open")

    async def test_success_resets_breaker_state(self):
        self.gateway.circuit_breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=0)
        primary_key = self.gateway._circuit_key(self.gateway.primary_target, "chat")
        self.gateway.circuit_breaker.record_failure(primary_key, counts=True)  # open
        self.assertTrue(self.gateway.circuit_breaker.can_attempt(primary_key))  # → half_open
        fake_response = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        request = self.gateway._build_request(
            request_type="chat", action="chat", user_id=None,
            prompt_template=None, prompt_version=None, trace_id=None,
            messages=[{"role": "user", "content": "hi"}],
        )
        with patch.object(self.gateway, "_post_json_with_retry", new=AsyncMock(return_value=fake_response)), patch.object(
            self.gateway, "_record_usage",
        ):
            result = await self.gateway._request_text_once(
                target=self.gateway.primary_target,
                request=request,
                policy=get_task_policy("chat"),
                routing_stage="initial",
            )

        self.assertEqual(result, "ok")
        self.assertEqual(self.gateway.circuit_breaker.state(primary_key), "closed")

    async def test_validation_error_does_not_open_breaker(self):
        async def fail_post(client, *, url, payload, headers, retries=3):
            raise _http_error(400)

        with patch.object(self.gateway, "_post_json_with_retry", new=AsyncMock(side_effect=fail_post)):
            with self.assertRaises(httpx.HTTPStatusError):
                await self.gateway.generate("请审查合同付款条款", action="legal_consultation")

        primary_key = self.gateway._circuit_key(self.gateway.primary_target, "chat")
        self.assertEqual(self.gateway.circuit_breaker.state(primary_key), "closed")


class CircuitBackendUnitTests(unittest.TestCase):
    def test_in_memory_backend_roundtrip(self):
        backend = InMemoryCircuitBackend()
        self.assertIsNone(backend.get("k"))
        backend.set("k", {"state": "open", "consecutive_failures": 1, "opened_at": 0.0, "half_open_probes": 0})
        self.assertEqual(backend.get("k")["state"], "open")
        backend.clear()
        self.assertIsNone(backend.get("k"))


if __name__ == "__main__":
    unittest.main()
