"""TaskPolicy / ModelRequest / 统一失败分类 / 重试与 fallback 边界 / trace 透传 专项测试。"""

import json
import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.core.llm_client import ModelGateway, settings
from app.core.llm_provider_adapter import provider_adapter
from app.core.model_policy import (
    EMBED_TIMEOUT_SECONDS,
    VISION_TIMEOUT_SECONDS,
    ModelError,
    ModelErrorKind,
    ModelRequest,
    TaskPolicy,
    classify_error,
    get_task_policy,
    new_trace_id,
)
from app.models.llm_call_log import LLMCallLog


class _FakeOkResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


def _http_error(status: int, body: str = "{}") -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    response = httpx.Response(status, request=request, text=body)
    return httpx.HTTPStatusError(f"HTTP {status}", request=request, response=response)


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

    def _stop_gateway_settings(self):
        for item in reversed(self.setting_patches):
            item.stop()


class TaskPolicyTests(unittest.TestCase):
    def test_core_tasks_have_policies(self):
        for task in ("chat", "embedding", "vision", "rerank"):
            policy = get_task_policy(task)
            self.assertEqual(policy.task, task)
            self.assertIsInstance(policy, TaskPolicy)

    def test_old_action_maps_to_default_policy(self):
        for action in ("legal_consultation", "document_summary", "rag_answer", "unknown_action_xyz"):
            self.assertEqual(get_task_policy(action).task, "chat", action)

    def test_action_maps_to_specialized_policy(self):
        self.assertEqual(get_task_policy("embedding").task, "embedding")
        self.assertEqual(get_task_policy("generate_with_images").task, "vision")
        self.assertEqual(get_task_policy("rag_rerank").task, "rerank")
        self.assertEqual(get_task_policy("rerank").task, "rerank")

    def test_policy_fields_independently_configured(self):
        chat = get_task_policy("chat")
        embedding = get_task_policy("embedding")
        vision = get_task_policy("vision")
        rerank = get_task_policy("rerank")
        self.assertIsNone(chat.timeout_seconds)
        self.assertEqual(embedding.timeout_seconds, EMBED_TIMEOUT_SECONDS)
        self.assertEqual(vision.timeout_seconds, VISION_TIMEOUT_SECONDS)
        self.assertEqual(chat.temperature, 0.7)
        self.assertEqual(rerank.temperature, 0.0)
        self.assertEqual(chat.model_tier, "auto")
        self.assertEqual(embedding.model_tier, "primary")
        self.assertEqual(vision.model_tier, "primary")
        self.assertEqual(embedding.budget_category, "embedding")
        self.assertEqual(vision.rate_limit_category, "vision")
        self.assertEqual(rerank.budget_category, "text")

    def test_policy_is_frozen(self):
        with self.assertRaises(FrozenInstanceError):
            get_task_policy("chat").temperature = 0.9

    def test_model_request_roundtrip(self):
        request = ModelRequest(
            request_type="chat",
            messages=[{"role": "user", "content": "hi"}],
            action="chat",
            trace_id="t-1",
        )
        self.assertEqual(request.trace_id, "t-1")
        self.assertEqual(request.request_id, "")
        self.assertEqual(request.request_type, "chat")

    def test_new_trace_id_is_generated(self):
        self.assertTrue(new_trace_id())
        self.assertNotEqual(new_trace_id(), new_trace_id())


class ModelErrorClassificationTests(unittest.TestCase):
    def test_retryable_kinds(self):
        for kind in (
            ModelErrorKind.TIMEOUT,
            ModelErrorKind.TRANSPORT,
            ModelErrorKind.PROVIDER_5XX,
            ModelErrorKind.RATE_LIMITED,
        ):
            self.assertTrue(ModelError(kind=kind, message="x").retryable, kind)

    def test_non_retryable_kinds(self):
        for kind in (
            ModelErrorKind.VALIDATION,
            ModelErrorKind.AUTHENTICATION,
            ModelErrorKind.PERMISSION,
            ModelErrorKind.CONTENT_BLOCKED,
            ModelErrorKind.INVALID_RESPONSE,
            ModelErrorKind.CIRCUIT_OPEN,
            ModelErrorKind.UNKNOWN,
        ):
            self.assertFalse(ModelError(kind=kind, message="x").retryable, kind)

    def test_classify_timeout(self):
        error = classify_error(httpx.ReadTimeout("slow"))
        self.assertEqual(error.kind, ModelErrorKind.TIMEOUT)
        self.assertTrue(error.retryable)

    def test_classify_transport(self):
        for exc in (httpx.ConnectError("conn"), httpx.ProxyError("proxy"), httpx.RemoteProtocolError("proto")):
            error = classify_error(exc)
            self.assertEqual(error.kind, ModelErrorKind.TRANSPORT, type(exc).__name__)
            self.assertTrue(error.retryable)

    def test_classify_http_statuses(self):
        cases = [
            (400, ModelErrorKind.VALIDATION, False),
            (401, ModelErrorKind.AUTHENTICATION, False),
            (403, ModelErrorKind.PERMISSION, False),
            (429, ModelErrorKind.RATE_LIMITED, True),
            (500, ModelErrorKind.PROVIDER_5XX, True),
            (503, ModelErrorKind.PROVIDER_5XX, True),
        ]
        for status, kind, retryable in cases:
            error = classify_error(_http_error(status))
            self.assertEqual(error.kind, kind, status)
            self.assertEqual(error.retryable, retryable, status)

    def test_classify_content_blocked(self):
        error = classify_error(_http_error(400, '{"error":{"code":"content_filter"}}'))
        self.assertEqual(error.kind, ModelErrorKind.CONTENT_BLOCKED)
        self.assertFalse(error.retryable)

    def test_classify_invalid_response_and_unknown(self):
        self.assertEqual(classify_error(json.JSONDecodeError("x", "doc", 0)).kind, ModelErrorKind.INVALID_RESPONSE)
        self.assertEqual(classify_error(RuntimeError("boom")).kind, ModelErrorKind.UNKNOWN)

    def test_classify_model_error_passthrough(self):
        original = ModelError(kind=ModelErrorKind.TIMEOUT, message="x")
        self.assertIs(classify_error(original), original)


class ProviderAdapterMaxTokensTests(unittest.TestCase):
    def test_openai_payload_includes_max_tokens_only_when_set(self):
        adapter = provider_adapter("openai_compatible")
        payload = adapter.chat_payload("qwen-plus", [{"role": "user", "content": "hi"}], False, 0.7, max_tokens=100)
        self.assertEqual(payload["max_tokens"], 100)
        base = adapter.chat_payload("qwen-plus", [{"role": "user", "content": "hi"}], False, 0.7)
        self.assertNotIn("max_tokens", base)

    def test_ollama_payload_maps_max_tokens_to_num_predict(self):
        adapter = provider_adapter("ollama")
        payload = adapter.chat_payload("llama", [], False, 0.7, max_tokens=256)
        self.assertEqual(payload["options"]["num_predict"], 256)
        base = adapter.chat_payload("llama", [], False, 0.7)
        self.assertNotIn("num_predict", base["options"])


class _SyncGatewayTests(_GatewaySettingsMixin, unittest.TestCase):
    def setUp(self):
        self._start_gateway_settings()

    def tearDown(self):
        self._stop_gateway_settings()

    def test_temperature_resolved_from_policy(self):
        rerank = TaskPolicy(task="rerank", temperature=0.0)
        self.assertEqual(self.gateway._resolve_temperature(rerank, None), 0.0)
        self.assertEqual(self.gateway._resolve_temperature(rerank, 0.5), 0.5)
        chat = get_task_policy("chat")
        self.assertEqual(self.gateway._resolve_temperature(chat, None), 0.7)

    def test_policy_fallback_flag_blocks_candidate_fallback(self):
        policy = TaskPolicy(task="chat", fallback_enabled=False)
        targets = self.gateway._candidate_targets("请审查合同付款条款", "legal_consultation", policy=policy)
        self.assertEqual([t.role for t in targets], ["primary"])


class ModelGatewayRetryTests(_GatewaySettingsMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._start_gateway_settings()

    def tearDown(self):
        self._stop_gateway_settings()

    async def test_transient_error_retries_then_succeeds(self):
        attempts = []

        class FlakyClient:
            def __init__(self):
                self.calls = 0

            async def post(self, url, json=None, headers=None):
                self.calls += 1
                attempts.append(self.calls)
                if self.calls == 1:
                    raise httpx.ReadTimeout("slow")
                return _FakeOkResponse()

        client = FlakyClient()
        result = await self.gateway._post_json_with_retry(client, url="u", payload={}, headers={}, retries=2)
        self.assertEqual(result["choices"][0]["message"]["content"], "ok")
        self.assertEqual(client.calls, 2)

    async def test_transient_error_exhausts_retries_then_raises(self):
        class AlwaysFails:
            def __init__(self):
                self.calls = 0

            async def post(self, url, json=None, headers=None):
                self.calls += 1
                raise httpx.ConnectError("down")

        client = AlwaysFails()
        with self.assertRaises(httpx.ConnectError):
            await self.gateway._post_json_with_retry(client, url="u", payload={}, headers={}, retries=3)
        self.assertEqual(client.calls, 3)

    async def test_rate_limited_429_is_retried(self):
        class Flaky429:
            def __init__(self):
                self.calls = 0

            async def post(self, url, json=None, headers=None):
                self.calls += 1
                if self.calls == 1:
                    raise _http_error(429)
                return _FakeOkResponse()

        client = Flaky429()
        await self.gateway._post_json_with_retry(client, url="u", payload={}, headers={}, retries=2)
        self.assertEqual(client.calls, 2)

    async def test_validation_400_is_not_retried(self):
        class Client400:
            def __init__(self):
                self.calls = 0

            async def post(self, url, json=None, headers=None):
                self.calls += 1
                raise _http_error(400)

        client = Client400()
        with self.assertRaises(httpx.HTTPStatusError):
            await self.gateway._post_json_with_retry(client, url="u", payload={}, headers={}, retries=3)
        self.assertEqual(client.calls, 1)

    async def test_authentication_401_is_not_retried(self):
        class Client401:
            def __init__(self):
                self.calls = 0

            async def post(self, url, json=None, headers=None):
                self.calls += 1
                raise _http_error(401)

        client = Client401()
        with self.assertRaises(httpx.HTTPStatusError):
            await self.gateway._post_json_with_retry(client, url="u", payload={}, headers={}, retries=3)
        self.assertEqual(client.calls, 1)

    async def test_transient_failure_falls_back_to_alternate_target(self):
        calls = []

        async def fake_request(*, target, **kwargs):
            calls.append(target.role)
            if target.role == "primary":
                raise httpx.ReadTimeout("timeout")
            return "fallback ok"

        with patch.object(self.gateway, "_request_text_once", new=AsyncMock(side_effect=fake_request)):
            result = await self.gateway.generate("请审查合同付款条款", action="legal_consultation")

        self.assertEqual(result, "fallback ok")
        self.assertEqual(calls, ["primary", "small"])

    async def test_auth_401_does_not_fallback(self):
        calls = []

        async def fake_request(*, target, **kwargs):
            calls.append(target.role)
            raise _http_error(401)

        with patch.object(self.gateway, "_request_text_once", new=AsyncMock(side_effect=fake_request)):
            with self.assertRaises(httpx.HTTPStatusError):
                await self.gateway.generate("请审查合同付款条款", action="legal_consultation")

        self.assertEqual(calls, ["primary"])

    async def test_fallback_disabled_by_policy(self):
        calls = []

        async def fake_request(*, target, **kwargs):
            calls.append(target.role)
            raise httpx.ReadTimeout("timeout")

        disabled = TaskPolicy(task="chat", fallback_enabled=False)
        with patch.object(self.gateway, "_request_text_once", new=AsyncMock(side_effect=fake_request)), patch(
            "app.core.llm_client.get_task_policy", return_value=disabled,
        ):
            with self.assertRaises(httpx.ReadTimeout):
                await self.gateway.generate("请审查合同付款条款", action="legal_consultation")

        self.assertEqual(calls, ["primary"])

    async def test_trace_id_is_passed_through_and_recorded(self):
        captured = {}

        def fake_record_usage(data, model, action, duration_ms, user_id=None, **kwargs):
            captured["request_id"] = kwargs.get("request_id")
            captured["action"] = action

        with patch.object(self.gateway, "_post_json_with_retry", new=AsyncMock(return_value=_FakeOkResponse().json())), patch.object(
            self.gateway, "_record_usage", side_effect=fake_record_usage,
        ):
            await self.gateway.chat([{"role": "user", "content": "你好"}], action="chat", trace_id="trace-abc-123")

        self.assertEqual(captured["request_id"], "trace-abc-123")
        self.assertEqual(captured["action"], "chat")

    async def test_trace_id_auto_generated_when_not_provided(self):
        captured = {}

        def fake_record_usage(data, model, action, duration_ms, user_id=None, **kwargs):
            captured["request_id"] = kwargs.get("request_id")

        with patch.object(self.gateway, "_post_json_with_retry", new=AsyncMock(return_value=_FakeOkResponse().json())), patch.object(
            self.gateway, "_record_usage", side_effect=fake_record_usage,
        ):
            await self.gateway.chat([{"role": "user", "content": "你好"}], action="chat")

        self.assertTrue(captured["request_id"])

    async def test_trace_shared_across_initial_and_fallback_targets(self):
        recorded = []
        calls = []

        def fake_record_usage(data, model, action, duration_ms, user_id=None, **kwargs):
            recorded.append((kwargs.get("request_id"), kwargs.get("routing_stage")))

        async def fake_post(client, *, url, payload, headers, retries=3):
            calls.append(payload["model"])
            if len(calls) == 1:
                raise httpx.ReadTimeout("timeout")
            return _FakeOkResponse().json()

        with patch.object(self.gateway, "_post_json_with_retry", new=AsyncMock(side_effect=fake_post)), patch.object(
            self.gateway, "_record_usage", side_effect=fake_record_usage,
        ):
            result = await self.gateway.generate("请审查合同付款条款", action="legal_consultation", trace_id="trace-shared")

        self.assertEqual(result, "ok")
        self.assertEqual(calls, ["qwen-plus", "qwen-turbo"])
        self.assertEqual({request_id for request_id, _ in recorded}, {"trace-shared"})
        self.assertEqual({stage for _, stage in recorded}, {"initial", "fallback"})

    async def test_call_log_records_trace_and_redacts_body(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

        fake_response = _FakeOkResponse().json()
        with patch.object(self.gateway, "_post_json_with_retry", new=AsyncMock(return_value=fake_response)), patch(
            "app.core.database.SessionLocal", TestingSessionLocal,
        ):
            await self.gateway.chat(
                [{"role": "user", "content": "这是敏感合同正文内容"}], action="chat", trace_id="trace-log-1",
            )

        db = TestingSessionLocal()
        try:
            row = db.query(LLMCallLog).one()
        finally:
            db.close()

        self.assertEqual(row.request_id, "trace-log-1")
        self.assertIsNotNone(row.request_excerpt)
        self.assertNotIn("敏感合同正文", row.request_excerpt)
        self.assertIn("redacted", row.request_excerpt)


if __name__ == "__main__":
    unittest.main()
