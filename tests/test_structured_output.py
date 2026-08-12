"""结构化输出 / JSON Schema 专项测试。"""

import unittest
from unittest.mock import AsyncMock, patch

from pydantic import BaseModel

from app.core.llm_client import ModelGateway, settings
from app.core.model_policy import ModelError, ModelErrorKind, TaskPolicy
from app.core.structured_output import (
    SchemaSpec,
    build_repair_prompt,
    extract_json_candidate,
    normalize_schema,
    parse_structured_output,
)
from app.services.llm_governance_service import LLMGovernanceError, llm_governance_service

_CONTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "amount": {"type": "number"},
    },
    "required": ["title", "amount"],
}

_CONTRACT_OK = '{"title": "合同", "amount": 100}'
_CONTRACT_BAD_MISSING_AMOUNT = '{"title": "合同"}'
_CONTRACT_BAD_AMOUNT_TYPE = '{"title": "合同", "amount": "一百"}'
_FENCED_OK = '```json\n{"title": "合同", "amount": 100}\n```'
_PROSE_WRAPPED = '好的，这是结果：\n{"title": "合同", "amount": 100}\n希望对你有帮助。'


class _ContractModel(BaseModel):
    title: str
    amount: float


class ExtractJsonCandidateTests(unittest.TestCase):
    def test_plain_json_object(self):
        self.assertEqual(extract_json_candidate(_CONTRACT_OK), _CONTRACT_OK)

    def test_markdown_fenced_json(self):
        self.assertEqual(extract_json_candidate(_FENCED_OK), _CONTRACT_OK)

    def test_prose_around_json(self):
        self.assertEqual(extract_json_candidate(_PROSE_WRAPPED), _CONTRACT_OK)

    def test_json_array_candidate(self):
        self.assertEqual(extract_json_candidate("结果是 [1, 2, 3]"), "[1, 2, 3]")

    def test_no_json_returns_none(self):
        self.assertIsNone(extract_json_candidate("模型没有返回任何 JSON"))
        self.assertIsNone(extract_json_candidate(""))
        self.assertIsNone(extract_json_candidate(None))

    def test_bracket_in_prose_skipped(self):
        self.assertEqual(extract_json_candidate('注意 { 这个括号，结果：{"title": "合同"}'), '{"title": "合同"}')


class NormalizeSchemaTests(unittest.TestCase):
    def test_dict_schema(self):
        spec = normalize_schema(_CONTRACT_SCHEMA)
        self.assertIsInstance(spec, SchemaSpec)
        self.assertEqual(spec.json_schema, _CONTRACT_SCHEMA)
        self.assertIsNone(spec.validate({"title": "合同", "amount": 100}))
        self.assertIsNotNone(spec.validate({"title": "合同"}))

    def test_pydantic_model(self):
        spec = normalize_schema(_ContractModel)
        self.assertIsInstance(spec, SchemaSpec)
        self.assertIsNone(spec.validate({"title": "合同", "amount": 100}))
        self.assertIsNotNone(spec.validate({"title": "合同", "amount": "一百"}))
        self.assertIsNotNone(spec.validate({"title": "合同"}))

    def test_unsupported_type_raises_type_error(self):
        with self.assertRaises(TypeError):
            normalize_schema(123)
        with self.assertRaises(TypeError):
            normalize_schema(None)


class ParseStructuredOutputTests(unittest.TestCase):
    def test_valid_json(self):
        spec = normalize_schema(_CONTRACT_SCHEMA)
        data, kind = parse_structured_output(_CONTRACT_OK, spec)
        self.assertEqual(data, {"title": "合同", "amount": 100})
        self.assertIsNone(kind)

    def test_markdown_wrapped(self):
        spec = normalize_schema(_CONTRACT_SCHEMA)
        data, kind = parse_structured_output(_FENCED_OK, spec)
        self.assertEqual(data["title"], "合同")
        self.assertIsNone(kind)

    def test_schema_mismatch(self):
        spec = normalize_schema(_CONTRACT_SCHEMA)
        data, kind = parse_structured_output(_CONTRACT_BAD_MISSING_AMOUNT, spec)
        self.assertIsNone(data)
        self.assertEqual(kind, ModelErrorKind.SCHEMA_VALIDATION_FAILED)

    def test_invalid_json(self):
        spec = normalize_schema(_CONTRACT_SCHEMA)
        data, kind = parse_structured_output("这是纯文本没有 JSON", spec)
        self.assertIsNone(data)
        self.assertEqual(kind, ModelErrorKind.INVALID_RESPONSE)

    def test_unparseable_candidate(self):
        spec = normalize_schema(_CONTRACT_SCHEMA)
        data, kind = parse_structured_output('{"title": "合同", "amount": }', spec)
        self.assertIsNone(data)
        self.assertEqual(kind, ModelErrorKind.INVALID_RESPONSE)


class BuildRepairPromptTests(unittest.TestCase):
    def test_carries_original_schema_and_raw_output(self):
        prompt = build_repair_prompt(_CONTRACT_SCHEMA, _CONTRACT_BAD_MISSING_AMOUNT)
        self.assertIn("JSON Schema", prompt)
        self.assertIn('"amount"', prompt)
        self.assertIn('"required"', prompt)
        self.assertIn(_CONTRACT_BAD_MISSING_AMOUNT, prompt)
        self.assertIn("只输出 JSON", prompt)
        self.assertIn("不得改变原有语义", prompt)

    def test_pydantic_schema_serialized(self):
        spec = normalize_schema(_ContractModel)
        prompt = build_repair_prompt(spec.json_schema, "bad")
        self.assertIn('"title"', prompt)
        self.assertIn('"amount"', prompt)


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


class StructuredGenerateTests(_GatewaySettingsMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._start_gateway_settings()

    def tearDown(self):
        self._stop_gateway_settings()

    def _patch_routing(self, outputs):
        return patch.object(
            self.gateway,
            "_request_text_with_routing",
            new=AsyncMock(side_effect=outputs),
        )

    async def test_returns_valid_dict(self):
        with self._patch_routing([_CONTRACT_OK]):
            result = await self.gateway.structured_generate("请生成合同信息", schema=_CONTRACT_SCHEMA)
        self.assertEqual(result, {"title": "合同", "amount": 100})

    async def test_accepts_markdown_wrapped_json(self):
        with self._patch_routing([_FENCED_OK]):
            result = await self.gateway.structured_generate("请生成合同信息", schema=_CONTRACT_SCHEMA)
        self.assertEqual(result["title"], "合同")

    async def test_accepts_prose_noise_around_json(self):
        with self._patch_routing([_PROSE_WRAPPED]):
            result = await self.gateway.structured_generate("请生成合同信息", schema=_CONTRACT_SCHEMA)
        self.assertEqual(result["amount"], 100)

    async def test_schema_mismatch_triggers_single_repair_and_succeeds(self):
        with self._patch_routing([_CONTRACT_BAD_MISSING_AMOUNT, _CONTRACT_OK]) as mocked:
            result = await self.gateway.structured_generate("请生成合同信息", schema=_CONTRACT_SCHEMA)
        self.assertEqual(result, {"title": "合同", "amount": 100})
        self.assertEqual(mocked.await_count, 2)

    async def test_invalid_json_triggers_repair(self):
        with self._patch_routing(["纯文本没有 JSON", _CONTRACT_OK]):
            result = await self.gateway.structured_generate("请生成合同信息", schema=_CONTRACT_SCHEMA)
        self.assertEqual(result["amount"], 100)

    async def test_repair_failed_raises_repair_failed(self):
        with self._patch_routing([_CONTRACT_BAD_MISSING_AMOUNT, _CONTRACT_BAD_MISSING_AMOUNT]):
            with self.assertRaises(ModelError) as ctx:
                await self.gateway.structured_generate("请生成合同信息", schema=_CONTRACT_SCHEMA)
        self.assertEqual(ctx.exception.kind, ModelErrorKind.REPAIR_FAILED)
        self.assertFalse(ctx.exception.retryable)

    async def test_no_infinite_retry(self):
        with self._patch_routing([_CONTRACT_BAD_MISSING_AMOUNT, _CONTRACT_BAD_MISSING_AMOUNT]) as mocked:
            with self.assertRaises(ModelError):
                await self.gateway.structured_generate("请生成合同信息", schema=_CONTRACT_SCHEMA)
        self.assertEqual(mocked.await_count, 2)

    async def test_repair_disabled_raises_first_kind(self):
        policy = TaskPolicy(task="chat", structured_repair_enabled=False)
        with patch("app.core.llm_client.get_task_policy", return_value=policy):
            with self._patch_routing([_CONTRACT_BAD_MISSING_AMOUNT]) as mocked:
                with self.assertRaises(ModelError) as ctx:
                    await self.gateway.structured_generate("请生成合同信息", schema=_CONTRACT_SCHEMA)
        self.assertEqual(ctx.exception.kind, ModelErrorKind.SCHEMA_VALIDATION_FAILED)
        self.assertEqual(mocked.await_count, 1)

    async def test_repair_disabled_invalid_json_raises_invalid_response(self):
        policy = TaskPolicy(task="chat", structured_repair_enabled=False)
        with patch("app.core.llm_client.get_task_policy", return_value=policy):
            with self._patch_routing(["纯文本"]) as mocked:
                with self.assertRaises(ModelError) as ctx:
                    await self.gateway.structured_generate("请生成合同信息", schema=_CONTRACT_SCHEMA)
        self.assertEqual(ctx.exception.kind, ModelErrorKind.INVALID_RESPONSE)
        self.assertEqual(mocked.await_count, 1)

    async def test_governance_enforced_before_llm_call(self):
        with patch.object(
            llm_governance_service,
            "enforce_generate_request",
            side_effect=LLMGovernanceError(status_code=429, code="X", message="限流", detail={}),
        ):
            with self._patch_routing([_CONTRACT_OK]) as mocked:
                with self.assertRaises(LLMGovernanceError):
                    await self.gateway.structured_generate("请生成合同信息", schema=_CONTRACT_SCHEMA)
        self.assertEqual(mocked.await_count, 0)

    async def test_repair_does_not_bypass_governance(self):
        calls = []

        def fake_enforce(**kwargs):
            calls.append(kwargs)
            return {}

        with patch.object(llm_governance_service, "enforce_generate_request", side_effect=fake_enforce):
            with self._patch_routing([_CONTRACT_BAD_MISSING_AMOUNT, _CONTRACT_OK]):
                result = await self.gateway.structured_generate("请生成合同信息", schema=_CONTRACT_SCHEMA)
        self.assertEqual(result, {"title": "合同", "amount": 100})
        self.assertEqual(len(calls), 2)  # 初始 + 修复，各自受治理约束
        self.assertEqual(calls[0]["action"], "generate")
        self.assertEqual(calls[1]["action"], "generate")
        self.assertIn("JSON Schema", calls[1]["prompt"])

    async def test_rejects_invalid_schema_type_before_llm(self):
        with self._patch_routing([_CONTRACT_OK]) as mocked:
            with self.assertRaises(TypeError):
                await self.gateway.structured_generate("请生成合同信息", schema=123)
        self.assertEqual(mocked.await_count, 0)

    async def test_accepts_pydantic_model(self):
        with self._patch_routing([_CONTRACT_OK]):
            result = await self.gateway.structured_generate("请生成合同信息", schema=_ContractModel)
        self.assertEqual(result, {"title": "合同", "amount": 100})

    async def test_pydantic_schema_mismatch_repairs(self):
        with self._patch_routing([_CONTRACT_BAD_AMOUNT_TYPE, _CONTRACT_OK]):
            result = await self.gateway.structured_generate("请生成合同信息", schema=_ContractModel)
        self.assertEqual(result["amount"], 100)

    async def test_repair_request_reuses_trace_id(self):
        seen = []

        async def fake_routing(*, source_text, request):
            seen.append(request.trace_id)
            return _CONTRACT_BAD_MISSING_AMOUNT if len(seen) == 1 else _CONTRACT_OK

        with patch.object(self.gateway, "_request_text_with_routing", new=AsyncMock(side_effect=fake_routing)):
            result = await self.gateway.structured_generate("请生成合同信息", schema=_CONTRACT_SCHEMA, trace_id="trace-123")
        self.assertEqual(result["amount"], 100)
        self.assertEqual(seen, ["trace-123", "trace-123"])

    async def test_repair_request_uses_generate_request_type(self):
        seen = []

        async def fake_routing(*, source_text, request):
            seen.append(request.request_type)
            return _CONTRACT_BAD_MISSING_AMOUNT if len(seen) == 1 else _CONTRACT_OK

        with patch.object(self.gateway, "_request_text_with_routing", new=AsyncMock(side_effect=fake_routing)):
            result = await self.gateway.structured_generate("请生成合同信息", schema=_CONTRACT_SCHEMA)
        self.assertEqual(result["amount"], 100)
        self.assertEqual(seen, ["generate", "generate"])


if __name__ == "__main__":
    unittest.main()
