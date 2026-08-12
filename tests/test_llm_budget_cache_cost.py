"""预算桶 / 限流桶 / LLM 响应缓存 / 按 attempt 成本统计 专项测试。"""

import unittest
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.database import Base
from app.core.llm_client import ModelGateway, settings
from app.core.response_cache import LLMResponseCache
from app.core.model_policy import TaskPolicy
from app.models.token_usage import TokenUsage
from app.models.user import User
from app.services.llm_governance_service import LLMGovernanceError, llm_governance_service
from app.services.token_service import token_service

_CONTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "amount": {"type": "number"},
    },
    "required": ["title", "amount"],
}

_OK_RESPONSE = {
    "choices": [{"message": {"content": "ok"}}],
    "usage": {"prompt_tokens": 12, "completion_tokens": 8},
}


class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value

    def delete(self, key):
        self.store.pop(key, None)


class TokenCostTests(unittest.TestCase):
    def test_compute_cost_by_pricing(self):
        cost = token_service.compute_cost("qwen-plus", 1000, 1000)
        self.assertAlmostEqual(cost, 0.004 + 0.012, places=6)

    def test_compute_cost_unknown_model_zero(self):
        self.assertEqual(token_service.compute_cost("unknown-model", 1000, 1000), 0.0)

    def test_compute_cost_zero_tokens(self):
        self.assertEqual(token_service.compute_cost("qwen-plus", 0, 0), 0.0)


class LLMResponseCacheUnitTests(unittest.TestCase):
    def test_in_process_ttl_expiry(self):
        now = [100.0]
        cache = LLMResponseCache(
            capacity=10, ttl_seconds=30, redis_enabled=False, redis_prefix="t", now=lambda: now[0],
        )
        cache.put("k1", "v1")
        self.assertEqual(cache.get("k1"), "v1")
        now[0] = 130.0
        self.assertIsNone(cache.get("k1"))

    def test_capacity_evicts_lru(self):
        cache = LLMResponseCache(capacity=2, ttl_seconds=60, redis_enabled=False, redis_prefix="t")
        cache.put("a", "1")
        cache.put("b", "2")
        self.assertEqual(cache.get("a"), "1")
        cache.put("c", "3")
        self.assertIsNone(cache.get("b"))
        self.assertEqual(cache.get("a"), "1")
        self.assertEqual(cache.get("c"), "3")

    def test_redis_backend_persists_across_instances(self):
        fake = FakeRedis()
        cache = LLMResponseCache(capacity=10, ttl_seconds=60, redis_enabled=True, redis_prefix="aibg:test")
        cache._redis = fake
        cache.put("digest-1", "hello")
        cache2 = LLMResponseCache(capacity=10, ttl_seconds=60, redis_enabled=True, redis_prefix="aibg:test")
        cache2._redis = fake
        self.assertEqual(cache2.get("digest-1"), "hello")

    def test_clear(self):
        cache = LLMResponseCache(capacity=10, ttl_seconds=60, redis_enabled=False, redis_prefix="t")
        cache.put("k", "v")
        cache.clear()
        self.assertIsNone(cache.get("k"))


class _GovernanceDBMixin:
    def _start_db(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        self.TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        self.db = self.TestingSessionLocal()
        self.user = User(username="bucket-user", email="bucket@example.com", hashed_password="x" * 16)
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)
        self.sessionlocal_patchers = [
            patch("app.services.llm_governance_service.SessionLocal", self.TestingSessionLocal),
            patch("app.services.llm_observability_service.SessionLocal", self.TestingSessionLocal),
        ]
        for patcher in self.sessionlocal_patchers:
            patcher.start()
        self.settings = get_settings()
        llm_governance_service.reset_local_state()

    def _stop_db(self):
        for patcher in reversed(self.sessionlocal_patchers):
            patcher.stop()
        llm_governance_service.reset_local_state()
        self.db.close()


class BudgetBucketTests(_GovernanceDBMixin, unittest.TestCase):
    def setUp(self):
        self._start_db()
        # 只测预算桶：关闭全局限流与全局预算，改用分桶配置。
        self._set_settings(
            LLM_RATE_LIMIT_WINDOW_SECONDS=0,
            LLM_RATE_LIMIT_MAX_REQUESTS=0,
            LLM_DAILY_REQUEST_LIMIT=0,
            LLM_DAILY_TOKEN_LIMIT=0,
            LLM_BUDGET_LIMITS_JSON='{"text": {"daily_requests": 1, "daily_tokens": 0},'
                                   '"embedding": {"daily_requests": 1, "daily_tokens": 0}}',
        )

    def tearDown(self):
        self._stop_db()

    def _set_settings(self, **overrides):
        for key, value in overrides.items():
            setattr(self.settings, key, value)

    def _seed_usage(self, category: str, count: int = 1) -> None:
        for _ in range(count):
            self.db.add(
                TokenUsage(
                    user_id=self.user.id,
                    model="qwen-plus",
                    action="chat",
                    budget_category=category,
                    prompt_tokens=1,
                    completion_tokens=1,
                    total_tokens=2,
                )
            )
        self.db.commit()

    def test_embedding_usage_does_not_consume_text_bucket(self):
        self._seed_usage("embedding", count=1)
        # embedding 桶已用 1，但 text 桶独立：chat 请求仍放行。
        result = llm_governance_service.enforce_chat_request(
            messages=[{"role": "user", "content": "你好"}], user_id=self.user.id, action="chat",
        )
        self.assertGreater(result["estimated_input_tokens"], 0)
        # embedding 桶自身已用满 1 → 拒绝。
        with self.assertRaises(LLMGovernanceError) as ctx:
            llm_governance_service.enforce_embedding_request(
                texts=["embedding 文本"], user_id=self.user.id, action="embedding",
            )
        self.assertEqual(ctx.exception.code, "LLM_DAILY_REQUEST_BUDGET_EXCEEDED")

    def test_text_bucket_blocks_after_exhaustion(self):
        self._seed_usage("text", count=1)
        with self.assertRaises(LLMGovernanceError) as ctx:
            llm_governance_service.enforce_chat_request(
                messages=[{"role": "user", "content": "你好"}], user_id=self.user.id, action="chat",
            )
        self.assertEqual(ctx.exception.code, "LLM_DAILY_REQUEST_BUDGET_EXCEEDED")


class RateLimitBucketTests(_GovernanceDBMixin, unittest.TestCase):
    def setUp(self):
        self._start_db()
        self._set_settings(
            LLM_RATE_LIMIT_WINDOW_SECONDS=60,
            LLM_RATE_LIMIT_MAX_REQUESTS=1,
            LLM_DAILY_REQUEST_LIMIT=0,
            LLM_DAILY_TOKEN_LIMIT=0,
        )

    def tearDown(self):
        self._stop_db()

    def _set_settings(self, **overrides):
        for key, value in overrides.items():
            setattr(self.settings, key, value)

    def test_chat_and_embedding_rate_buckets_are_independent(self):
        with patch.object(llm_governance_service, "_redis", return_value=None):
            # chat 桶第 1 次：放行
            llm_governance_service.enforce_chat_request(
                messages=[{"role": "user", "content": "第一条"}], user_id=self.user.id, action="chat",
            )
            # embedding 桶第 1 次：独立计数，放行
            llm_governance_service.enforce_embedding_request(
                texts=["embedding 文本"], user_id=self.user.id, action="embedding",
            )
            # chat 桶第 2 次：超限 → 429
            with self.assertRaises(LLMGovernanceError) as ctx:
                llm_governance_service.enforce_chat_request(
                    messages=[{"role": "user", "content": "第二条"}], user_id=self.user.id, action="chat",
                )
        self.assertEqual(ctx.exception.code, "LLM_RATE_LIMIT_EXCEEDED")

    def test_per_category_rate_config_override(self):
        self._set_settings(
            LLM_RATE_LIMIT_CONFIG_JSON='{"chat": {"window_seconds": 60, "max_requests": 1},'
                                       '"embedding": {"window_seconds": 60, "max_requests": 5}}',
        )
        with patch.object(llm_governance_service, "_redis", return_value=None):
            for _ in range(2):
                llm_governance_service.enforce_embedding_request(
                    texts=["e"], user_id=self.user.id, action="embedding",
                )
            # embedding 允许 5 次，chat 仅 1 次：embedding 第 2 次仍放行
            llm_governance_service.enforce_chat_request(
                messages=[{"role": "user", "content": "仅一次"}], user_id=self.user.id, action="chat",
            )
            with self.assertRaises(LLMGovernanceError) as ctx:
                llm_governance_service.enforce_chat_request(
                    messages=[{"role": "user", "content": "第二次"}], user_id=self.user.id, action="chat",
                )
        self.assertEqual(ctx.exception.code, "LLM_RATE_LIMIT_EXCEEDED")


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
            patch.object(settings, "LLM_RESPONSE_CACHE_ENABLED", True),
            patch.object(settings, "LLM_RESPONSE_CACHE_REDIS_ENABLED", False),
        ]
        for item in self.setting_patches:
            item.start()
        self.gateway = ModelGateway()

    def _stop_gateway_settings(self):
        for item in reversed(self.setting_patches):
            item.stop()

    def _patch_routing(self, outputs):
        return patch.object(
            self.gateway,
            "_request_text_with_routing",
            new=AsyncMock(side_effect=outputs),
        )


class ResponseCacheIntegrationTests(_GatewaySettingsMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._start_gateway_settings()

    def tearDown(self):
        self._stop_gateway_settings()

    async def test_cacheable_request_hits_second_call(self):
        with self._patch_routing(["hello"]) as mocked:
            first = await self.gateway.generate("请审查合同", cacheable=True)
            second = await self.gateway.generate("请审查合同", cacheable=True)
        self.assertEqual((first, second), ("hello", "hello"))
        self.assertEqual(mocked.await_count, 1)

    async def test_default_not_cached(self):
        with self._patch_routing(["hello", "hello"]) as mocked:
            await self.gateway.generate("请审查合同")
            await self.gateway.generate("请审查合同")
        self.assertEqual(mocked.await_count, 2)

    async def test_cache_global_switch_disables(self):
        with patch.object(settings, "LLM_RESPONSE_CACHE_ENABLED", False):
            with self._patch_routing(["hello", "hello"]) as mocked:
                await self.gateway.generate("请审查合同", cacheable=True)
                await self.gateway.generate("请审查合同", cacheable=True)
        self.assertEqual(mocked.await_count, 2)

    async def test_model_version_isolation(self):
        import dataclasses

        new_target = dataclasses.replace(self.gateway.primary_target, model="qwen-max")
        with self._patch_routing(["m1", "m2"]) as mocked:
            await self.gateway.generate("请审查合同", cacheable=True)
            with patch.object(self.gateway, "primary_target", new_target):
                await self.gateway.generate("请审查合同", cacheable=True)
        self.assertEqual(mocked.await_count, 2)

    async def test_prompt_version_isolation(self):
        with self._patch_routing(["v1", "v2"]) as mocked:
            await self.gateway.generate("请审查合同", cacheable=True, prompt_version=1)
            await self.gateway.generate("请审查合同", cacheable=True, prompt_version=2)
        self.assertEqual(mocked.await_count, 2)

    async def test_permission_fingerprint_isolation(self):
        with self._patch_routing(["p1", "p2"]) as mocked:
            await self.gateway.generate("请审查合同", cacheable=True, permission_fingerprint="perm-A")
            await self.gateway.generate("请审查合同", cacheable=True, permission_fingerprint="perm-B")
        self.assertEqual(mocked.await_count, 2)

    async def test_same_permission_fingerprint_hits(self):
        with self._patch_routing(["same"]) as mocked:
            await self.gateway.generate("请审查合同", cacheable=True, permission_fingerprint="perm-A")
            await self.gateway.generate("请审查合同", cacheable=True, permission_fingerprint="perm-A")
        self.assertEqual(mocked.await_count, 1)

    async def test_cache_hit_still_passes_governance(self):
        calls = []

        def fake_enforce(**kwargs):
            calls.append(kwargs)
            return {}

        with patch.object(llm_governance_service, "enforce_generate_request", side_effect=fake_enforce):
            with self._patch_routing(["hello"]):
                await self.gateway.generate("请审查合同", cacheable=True)
                await self.gateway.generate("请审查合同", cacheable=True)
        self.assertEqual(len(calls), 2)  # 命中仍先过治理门禁（权限/预算/限流）

    async def test_cache_key_is_irreversible_digest(self):
        with self._patch_routing(["hello"]):
            await self.gateway.generate(
                "请审查合同", cacheable=True,
                permission_fingerprint="role=lawyer:scope=doc-1:user-secret",
            )
        keys = list(self.gateway.response_cache._cache.keys())
        self.assertEqual(len(keys), 1)
        self.assertRegex(keys[0], r"^[0-9a-f]{64}$")
        self.assertNotIn("请审查合同", keys[0])
        self.assertNotIn("lawyer", keys[0])
        self.assertNotIn("user-secret", keys[0])

    async def test_structured_generate_cacheable_hits(self):
        with self._patch_routing(['{"title": "合同", "amount": 100}']) as mocked:
            first = await self.gateway.structured_generate("请生成合同", schema=_CONTRACT_SCHEMA, cacheable=True)
            second = await self.gateway.structured_generate("请生成合同", schema=_CONTRACT_SCHEMA, cacheable=True)
        self.assertEqual(first, {"title": "合同", "amount": 100})
        self.assertEqual(second, {"title": "合同", "amount": 100})
        self.assertEqual(mocked.await_count, 1)

    async def test_structured_generate_different_schema_no_cross_hit(self):
        other = {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}
        with self._patch_routing(['{"title": "合同", "amount": 100}', '{"title": "合同"}']) as mocked:
            await self.gateway.structured_generate("请生成合同", schema=_CONTRACT_SCHEMA, cacheable=True)
            await self.gateway.structured_generate("请生成合同", schema=other, cacheable=True)
        self.assertEqual(mocked.await_count, 2)


class AttemptCostAccountingTests(_GatewaySettingsMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._start_gateway_settings()

    def tearDown(self):
        self._stop_gateway_settings()

    def _make_testing_session(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        return sessionmaker(bind=engine, autoflush=False, autocommit=False)

    async def test_records_estimate_before_and_actual_after(self):
        captured = {}

        def fake_record_usage(data, model, action, duration_ms, user_id=None, **kwargs):
            captured.update(kwargs)

        with patch.object(self.gateway, "_post_json_with_retry", new=AsyncMock(return_value=_OK_RESPONSE)), patch.object(
            self.gateway, "_record_usage", side_effect=fake_record_usage,
        ):
            await self.gateway.generate("请审查合同付款条款", action="generate")

        self.assertEqual(captured["attempt_number"], 1)
        self.assertGreater(captured["estimated_input_tokens"], 0)
        self.assertEqual(captured["estimated_output_tokens"], settings.LLM_ESTIMATED_COMPLETION_TOKENS)
        self.assertIn("request_id", captured)

    async def test_retry_attempt_cost_distinguishable_from_final_success(self):
        recorded = []
        TestingSessionLocal = self._make_testing_session()

        def fake_record(model, db, user_id=None, action=None, prompt_tokens=0, completion_tokens=0,
                        duration_ms=None, budget_category=None, attempt_number=None, cost=None):
            # 镜像真实 token_service.record：cost=None 时按定价内部计算。
            if cost is None:
                cost = token_service.compute_cost(model, prompt_tokens, completion_tokens)
            recorded.append(
                {
                    "attempt_number": attempt_number,
                    "budget_category": budget_category,
                    "cost": cost,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                }
            )
            return None

        async def fake_post(client, *, url, payload, headers, retries=3):
            if not getattr(fake_post, "calls", 0):
                fake_post.calls = 0
            fake_post.calls += 1
            if fake_post.calls == 1:
                raise httpx.ReadTimeout("timeout")
            return _OK_RESPONSE

        with patch.object(self.gateway, "_post_json_with_retry", new=AsyncMock(side_effect=fake_post)), patch(
            "app.services.token_service.token_service.record", side_effect=fake_record,
        ), patch("app.core.database.SessionLocal", TestingSessionLocal), patch.object(
            settings,
            "LLM_MODEL_PRICING",
            '{"qwen-plus": {"input_per_1k": 0.004, "output_per_1k": 0.012},'
            '"qwen-turbo": {"input_per_1k": 0.002, "output_per_1k": 0.006}}',
        ):
            result = await self.gateway.generate("请审查合同付款条款", action="legal_consultation")

        self.assertEqual(result, "ok")
        # 第 1 次（primary 失败）与第 2 次（fallback 成功）各自记账，attempt_number 区分重试。
        self.assertEqual([r["attempt_number"] for r in recorded], [1, 2])
        self.assertEqual([r["budget_category"] for r in recorded], ["text", "text"])
        self.assertEqual(recorded[0]["prompt_tokens"], 0)
        self.assertEqual(recorded[0]["cost"], 0.0)
        self.assertGreater(recorded[1]["prompt_tokens"], 0)
        self.assertGreater(recorded[1]["cost"], 0.0)

    async def test_fallback_target_records_attempt_number(self):
        captured = []

        def fake_record_usage(data, model, action, duration_ms, user_id=None, **kwargs):
            captured.append((model, kwargs.get("attempt_number"), kwargs.get("routing_stage")))

        async def fake_post(client, *, url, payload, headers, retries=3):
            if not getattr(fake_post, "calls", 0):
                fake_post.calls = 0
            fake_post.calls += 1
            if fake_post.calls == 1:
                raise httpx.ReadTimeout("timeout")
            return _OK_RESPONSE

        with patch.object(self.gateway, "_post_json_with_retry", new=AsyncMock(side_effect=fake_post)), patch.object(
            self.gateway, "_record_usage", side_effect=fake_record_usage,
        ):
            result = await self.gateway.generate("请审查合同付款条款", action="legal_consultation")

        self.assertEqual(result, "ok")
        self.assertEqual(captured, [("qwen-plus", 1, "initial"), ("qwen-turbo", 2, "fallback")])


if __name__ == "__main__":
    unittest.main()
