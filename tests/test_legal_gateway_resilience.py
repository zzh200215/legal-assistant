"""E 项：LLM 网关可靠性——超时有界、异常不穿透、降级如实标注。

背景（`docs/review/step7-copilot-server-trace.md` / 诊断文档 E 项）：
真机上出现过两类网关问题：① `RuntimeError: Event loop is closed`（连接池里的 keep-alive
连接属于已关闭的临时事件循环，根因已修，回归见 `tests/test_llm_client_compat.py` 的
`ClientPoolEventLoopTests`）；② "长时间不返回"。

本文件守的是第 ② 类的**产品行为边界**：
- 网关抛超时/连接错误时，`_llm_chat` 必须吞掉异常返回 None（不能让异常穿透到接口层）；
- 咨询链路必须仍然给出可用答复（规则兜底），并把生成步如实标成 `degraded`；
- 客户端必须带配置里的超时创建（"不返回"是有界的，不是无限等）。
"""
import asyncio
import unittest
from datetime import date
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.core.llm_client import LLMClient, settings
from app.models.legal import LegalSource
from app.services.legal.legal_service import _llm_chat, consultation_payload


class GatewayTimeoutDoesNotLeakTests(unittest.IsolatedAsyncioTestCase):
    """网关超时 → 吞掉异常 → 咨询链路有界降级。"""

    async def test_llm_chat_returns_none_when_gateway_times_out(self):
        with patch(
            "app.services.llm.llm_service.llm_service.generate",
            new=AsyncMock(side_effect=httpx.ReadTimeout("gateway timed out")),
        ):
            result = await _llm_chat("请审查这份合同", user_id=1)

        self.assertIsNone(result)

    async def test_llm_chat_returns_none_on_connection_error(self):
        with patch(
            "app.services.llm.llm_service.llm_service.generate",
            new=AsyncMock(side_effect=httpx.ConnectError("gateway unreachable")),
        ):
            result = await _llm_chat("请审查这份合同", user_id=1)

        self.assertIsNone(result)

    async def test_consultation_payload_survives_gateway_timeout(self):
        """超时后仍然返回可用答复，并在 trace 里如实写 degraded。"""
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        db = Session()
        try:
            source = LegalSource(
                user_id=1,
                title="劳动合同法",
                citation="劳动合同法第40条",
                source_type="statute",
                status="active",
                version="v1",
                effective_date=date(2020, 1, 1),
                content="用人单位提前三十日以书面形式通知劳动者本人，可以解除劳动合同。",
            )
            db.add(source)
            db.commit()
            db.refresh(source)

            trace: dict = {}
            with patch(
                "app.services.llm.llm_service.llm_service.generate",
                new=AsyncMock(side_effect=httpx.ReadTimeout("gateway timed out")),
            ):
                category, known, missing, refs, advice, risk, status = await consultation_payload(
                    "公司要我明天就走，合法吗？",
                    [source],
                    user_id=1,
                    db=db,
                    trace_out=trace,
                )

            # 仍然给出可用答复（规则兜底），不是 500、不是空白
            self.assertTrue(advice)
            self.assertTrue(category)
            self.assertIn(status, {"needs_lawyer_review", "pending_review"})

            by_id = {step["id"]: step for step in trace["steps"]}
            self.assertEqual(by_id["generate"]["status"], "degraded")
            self.assertIn("规则兜底", by_id["generate"]["detail"])
            # 降级不等于假装成功：metrics 仍然记录，便于事后统计降级率
            self.assertIn("metrics", trace)
        finally:
            db.close()
            engine.dispose()


class GatewayTimeoutIsBoundedTests(unittest.TestCase):
    """客户端必须带超时创建——"长时间不返回"有界。"""

    def test_client_is_created_with_the_configured_timeout(self):
        captured: dict = {}

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                captured.update(kwargs)

            async def aclose(self):
                return None

        gateway = LLMClient()
        with patch("app.core.llm_client.httpx.AsyncClient", FakeAsyncClient):
            gateway._get_client(gateway.primary_target, timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS)

        self.assertEqual(captured.get("timeout"), settings.LLM_REQUEST_TIMEOUT_SECONDS)
        self.assertGreater(settings.LLM_REQUEST_TIMEOUT_SECONDS, 0)

    def test_retry_budget_is_finite(self):
        """重试次数必须有界，否则"不返回"会被重试放大。"""
        self.assertGreaterEqual(settings.LLM_PRIMARY_REQUEST_RETRIES, 1)
        self.assertLessEqual(settings.LLM_PRIMARY_REQUEST_RETRIES, 5)
        self.assertGreaterEqual(settings.LLM_FALLBACK_REQUEST_RETRIES, 1)
        self.assertLessEqual(settings.LLM_FALLBACK_REQUEST_RETRIES, 3)

    def test_timeout_can_be_overridden_by_config(self):
        """超时值来自配置，可调（本机默认 60s）。"""
        with patch.object(settings, "LLM_REQUEST_TIMEOUT_SECONDS", 12):
            captured: dict = {}

            class FakeAsyncClient:
                def __init__(self, *args, **kwargs):
                    captured.update(kwargs)

                async def aclose(self):
                    return None

            gateway = LLMClient()
            with patch("app.core.llm_client.httpx.AsyncClient", FakeAsyncClient):
                gateway._get_client(gateway.primary_target, timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS)

        self.assertEqual(captured.get("timeout"), 12)


if __name__ == "__main__":
    unittest.main()
