import json
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import create_access_token
from app.core.auth import hash_password
from app.core.config import get_settings
from app.core.database import Base, get_db
from app.core import database as database_module
from app.main import app
from app.models.agent import AgentRun, ToolCallLog
from app.models.connector import ConnectorSyncJob, ExternalConnector
from app.models.document import Document, DocumentQARecord
from app.models.email import EmailDraft
from app.models.llm_call_log import LLMCallLog
from app.models.meeting import Meeting
from app.models.operation_log import OperationLog
from app.models.prompt import PromptTemplate, PromptTemplateVersion
from app.models.task import Task
from app.models.token_usage import TokenUsage
from app.models.user import User
from app.services.llm_governance_service import llm_governance_service
from app.tools.base import BaseAgentTool, tool_success


class FakeTool(BaseAgentTool):
    def __init__(self, name, description, auto_context_fields=(), handler=None, parameters=None):
        self.name = name
        self.description = description
        self.auto_context_fields = auto_context_fields
        self.parameters = parameters or {"type": "object", "properties": {}, "required": []}
        self._handler = handler or (lambda **kwargs: tool_success("ok", kwargs))

    async def run(self, **kwargs):
        return self._handler(**kwargs)


class ApiStabilityTests(unittest.TestCase):
    def _ws_subprotocols(self):
        return ["json", f"bearer.{self.token}"]

    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        self.db = self.TestingSessionLocal()

        self.user = User(
            username="tester",
            email="tester@example.com",
            hashed_password=hash_password("secret"),
        )
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

        self.document = Document(
            user_id=self.user.id,
            title="spec.md",
            file_path="uploads/spec.md",
            file_type="md",
            status="indexed",
        )
        self.db.add(self.document)
        self.meeting = Meeting(
            user_id=self.user.id,
            title="项目例会",
            transcript="本周由李雷负责同步客户更新时间。",
            status="pending",
        )
        self.db.add(self.meeting)
        self.db.commit()
        self.db.refresh(self.document)
        self.db.refresh(self.meeting)
        self.settings = get_settings()
        self._governance_setting_keys = [
            "LLM_RATE_LIMIT_WINDOW_SECONDS",
            "LLM_RATE_LIMIT_MAX_REQUESTS",
            "LLM_DAILY_REQUEST_LIMIT",
            "LLM_DAILY_TOKEN_LIMIT",
            "LLM_ESTIMATED_CHARS_PER_TOKEN",
            "LLM_ESTIMATED_COMPLETION_TOKENS",
        ]
        self._governance_settings_backup = {
            key: getattr(self.settings, key)
            for key in self._governance_setting_keys
        }
        llm_governance_service.reset_local_state()
        self.sessionlocal_patchers = [
            patch("app.services.llm_governance_service.SessionLocal", self.TestingSessionLocal),
            patch("app.services.llm_observability_service.SessionLocal", self.TestingSessionLocal),
            patch("app.core.database.SessionLocal", self.TestingSessionLocal),
        ]
        for patcher in self.sessionlocal_patchers:
            patcher.start()

        def override_get_db():
            db = self.TestingSessionLocal()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)
        self.token = create_access_token({"sub": self.user.id})
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def tearDown(self):
        app.dependency_overrides.clear()
        for patcher in reversed(getattr(self, "sessionlocal_patchers", [])):
            patcher.stop()
        for key, value in self._governance_settings_backup.items():
            setattr(self.settings, key, value)
        llm_governance_service.reset_local_state()
        self.db.close()

    def _set_governance_settings(self, **overrides):
        for key, value in overrides.items():
            setattr(self.settings, key, value)
        llm_governance_service.reset_local_state()

    def test_chat_request_validates_message_role(self):
        response = self.client.post(
            "/api/chat/",
            headers=self.headers,
            json={"messages": [{"role": "invalid", "content": "hello"}]},
        )

        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], "VALIDATION_ERROR")

    def test_chat_request_returns_stable_rate_limit_code(self):
        self._set_governance_settings(
            LLM_RATE_LIMIT_WINDOW_SECONDS=60,
            LLM_RATE_LIMIT_MAX_REQUESTS=1,
            LLM_DAILY_REQUEST_LIMIT=0,
            LLM_DAILY_TOKEN_LIMIT=0,
        )

        fake_response = {
            "choices": [{"message": {"content": "收到"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 8},
        }
        with patch("app.core.llm_client.LLMClient._post_json_with_retry", new=AsyncMock(return_value=fake_response)):
            first = self.client.post(
                "/api/chat/",
                headers=self.headers,
                json={"messages": [{"role": "user", "content": "第一条消息"}]},
            )
            second = self.client.post(
                "/api/chat/",
                headers=self.headers,
                json={"messages": [{"role": "user", "content": "第二条消息"}]},
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        payload = second.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], "LLM_RATE_LIMIT_EXCEEDED")
        self.assertEqual(payload["message"], "请求过于频繁，请稍后再试")

    def test_chat_request_returns_stable_daily_token_budget_code(self):
        self._set_governance_settings(
            LLM_RATE_LIMIT_WINDOW_SECONDS=0,
            LLM_RATE_LIMIT_MAX_REQUESTS=0,
            LLM_DAILY_REQUEST_LIMIT=0,
            LLM_DAILY_TOKEN_LIMIT=100,
            LLM_ESTIMATED_CHARS_PER_TOKEN=1,
            LLM_ESTIMATED_COMPLETION_TOKENS=40,
        )
        self.db.add(
            TokenUsage(
                user_id=self.user.id,
                model="qwen-plus",
                action="chat",
                prompt_tokens=30,
                completion_tokens=30,
                total_tokens=60,
            )
        )
        self.db.commit()

        response = self.client.post(
            "/api/chat/",
            headers=self.headers,
            json={"messages": [{"role": "user", "content": "这是一条会触发预算拦截的消息"}]},
        )

        self.assertEqual(response.status_code, 429)
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], "LLM_DAILY_TOKEN_BUDGET_EXCEEDED")
        self.assertEqual(payload["message"], "今日 Token 预算已用尽")

    def test_chat_request_500_does_not_leak_internal_error_detail(self):
        with patch(
            "app.api.chat_api.llm_service.chat",
            new=AsyncMock(side_effect=RuntimeError("api-key=test-secret")),
        ):
            response = self.client.post(
                "/api/chat/",
                headers=self.headers,
                json={"messages": [{"role": "user", "content": "你好"}]},
            )

        self.assertEqual(response.status_code, 500)
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], "CHAT_REQUEST_FAILED")
        self.assertEqual(payload["error"]["detail"], "聊天请求失败")
        self.assertNotIn("test-secret", json.dumps(payload, ensure_ascii=False))

    def test_ws_agent_rejects_invalid_max_steps(self):
        with self.client.websocket_connect("/api/ws/agent", subprotocols=self._ws_subprotocols(), timeout=5) as websocket:
            websocket.send_json({"goal": "生成计划", "max_steps": 99})
            message = websocket.receive_json()

        self.assertEqual(message["type"], "error")
        self.assertIn("max_steps", message["message"])

    def test_ws_agent_internal_error_does_not_leak_detail(self):
        with patch(
            "app.api.ws_api.agent_service.run",
            new=AsyncMock(side_effect=RuntimeError("db_password=secret")),
        ):
            with self.client.websocket_connect("/api/ws/agent", subprotocols=self._ws_subprotocols(), timeout=5) as websocket:
                websocket.send_json({"goal": "生成计划"})
                message = websocket.receive_json()

        self.assertEqual(message["type"], "error")
        self.assertEqual(message["message"], "请求失败")
        self.assertNotIn("secret", json.dumps(message, ensure_ascii=False))

    def test_ws_agent_can_resume_approval_run(self):
        fake_run = type(
            "FakeRun",
            (),
            {
                "id": 12,
                "user_id": self.user.id,
                "session_id": None,
                "goal": "审批恢复",
                "status": "completed",
                "result": '{"final_answer":"已恢复","artifacts":{"documents":[],"tasks":[],"emails":[],"meetings":[]}}',
                "final_answer": "已恢复",
                "last_observation": "",
                "failure_reason": None,
                "total_steps": 2,
                "error": None,
                "created_at": None,
                "completed_at": None,
            },
        )()

        with patch(
            "app.api.ws_api.agent_service.resume_after_approval",
            new=AsyncMock(return_value=fake_run),
        ), patch(
            "app.api.ws_api.agent_service.get_run_logs",
            return_value=[],
        ), patch(
            "app.api.ws_api.agent_service.serialize_run",
            return_value={"id": 12, "status": "completed", "artifacts": {}},
        ):
            with self.client.websocket_connect("/api/ws/agent", subprotocols=self._ws_subprotocols(), timeout=5) as websocket:
                websocket.send_json({"action": "resume_approval", "approval_id": 9})
                message = websocket.receive_json()

        self.assertEqual(message["type"], "run_snapshot")
        self.assertEqual(message["run"]["id"], 12)
        self.assertEqual(message["run"]["status"], "completed")

    def test_agent_run_detail_keeps_contract_and_includes_artifacts(self):
        from app.models.agent import AgentRun

        run = AgentRun(
            user_id=self.user.id,
            goal="总结文档 1，并提取其中的风险点",
            status="completed",
            result='{"final_answer":"已完成","artifacts":{"documents":[{"document_id":1}],"tasks":[],"emails":[],"meetings":[]},"supervisor_plan":{"intent":"文档风险分析","workers":["document_agent"],"risk_level":"low"}}',
            final_answer="已完成",
            total_steps=3,
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)

        response = self.client.get(f"/api/agent/runs/{run.id}", headers=self.headers)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        data = payload["data"]
        self.assertEqual(data["final_answer"], "已完成")
        self.assertIn("artifacts", data)
        self.assertEqual(data["artifacts"]["documents"][0]["document_id"], 1)
        self.assertEqual(data["supervisor_plan"]["workers"], ["document_agent"])

    def test_ws_chat_rejects_too_long_content(self):
        with self.client.websocket_connect("/api/ws/chat", subprotocols=self._ws_subprotocols(), timeout=5) as websocket:
            websocket.send_json({"content": "a" * 8001})
            message = websocket.receive_json()

        self.assertEqual(message["type"], "error")
        self.assertIn("消息长度不能超过", message["content"])

    def test_ws_chat_internal_error_does_not_leak_detail(self):
        async def _raise_stream(*_args, **_kwargs):
            raise RuntimeError("token=secret")
            yield

        with patch("app.api.ws_api.llm_client.chat_stream", side_effect=_raise_stream):
            with self.client.websocket_connect("/api/ws/chat", subprotocols=self._ws_subprotocols(), timeout=5) as websocket:
                websocket.send_json({"content": "你好"})
                session_msg = websocket.receive_json()
                message = websocket.receive_json()

        self.assertEqual(session_msg["type"], "session")
        self.assertEqual(message["type"], "error")
        self.assertEqual(message["content"], "请求失败")
        self.assertNotIn("secret", json.dumps(message, ensure_ascii=False))

    def test_ws_chat_records_document_qa(self):
        fake_result = {
            "answer": "首付款应在 2026-07-01 前支付 100 万元。",
            "citations": [
                {
                    "document_id": self.document.id,
                    "chunk_id": 1,
                    "chunk_index": 0,
                    "page_number": 3,
                    "section_title": "付款条款",
                    "locator": f"doc:{self.document.id} | page:3 | section:付款条款 | chunk:0",
                    "source_text": "付款条款：甲方应在 2026-07-01 前支付首付款 100 万元。",
                }
            ],
            "confidence": 0.81,
            "can_answer": True,
            "refusal_reason": None,
            "hit_chunks": [
                {
                    "content": "付款条款：甲方应在 2026-07-01 前支付首付款 100 万元。",
                    "metadata": {"chunk_index": 0, "page_number": 3, "section_title": "付款条款"},
                }
            ],
            "latency_ms": 12,
            "agentic_rag": {"enabled": True, "retrieval_rounds": 1, "steps": []},
        }

        with patch("app.api.ws_api.agentic_rag_service.answer_async", new=AsyncMock(return_value=fake_result)):
            with self.client.websocket_connect("/api/ws/chat", subprotocols=self._ws_subprotocols(), timeout=5) as websocket:
                websocket.send_json({"content": "付款条件是什么", "document_id": self.document.id})
                session_msg = websocket.receive_json()
                done = websocket.receive_json()

        self.assertEqual(session_msg["type"], "session")
        self.assertEqual(done["type"], "done")
        self.assertTrue(done["can_answer"])
        self.assertGreater(done["confidence"], 0.5)
        self.assertIsNone(done["refusal_reason"])
        self.assertEqual(done["agentic_rag"]["retrieval_rounds"], 1)
        self.assertEqual(done["citations"][0]["document_id"], self.document.id)
        self.assertEqual(done["citations"][0]["page_number"], 3)

        db = self.TestingSessionLocal()
        try:
            records = (
                db.query(DocumentQARecord)
                .filter_by(document_id=self.document.id, user_id=self.user.id)
                .all()
            )
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].question, "付款条件是什么")
            self.assertEqual(records[0].answer, fake_result["answer"])
            self.assertEqual(records[0].source, "ws_chat")
        finally:
            db.close()

    def test_document_and_task_list_use_paginated_structure(self):
        task = Task(
            user_id=self.user.id,
            title="跟进付款",
            status="todo",
            priority="medium",
        )
        self.db.add(task)
        self.db.commit()

        document_response = self.client.get("/api/documents/?page=1&page_size=10", headers=self.headers)
        task_response = self.client.get("/api/tasks/?page=1&page_size=10", headers=self.headers)

        self.assertEqual(document_response.status_code, 200)
        self.assertEqual(task_response.status_code, 200)

        document_payload = document_response.json()
        task_payload = task_response.json()

        self.assertTrue(document_payload["success"])
        self.assertEqual(document_payload["data"]["page"], 1)
        self.assertEqual(document_payload["data"]["page_size"], 10)
        self.assertIsInstance(document_payload["data"]["items"], list)
        self.assertGreaterEqual(document_payload["data"]["total"], 1)

        self.assertTrue(task_payload["success"])
        self.assertEqual(task_payload["data"]["page"], 1)
        self.assertEqual(task_payload["data"]["page_size"], 10)
        self.assertIsInstance(task_payload["data"]["items"], list)
        self.assertGreaterEqual(task_payload["data"]["total"], 1)

    def test_document_not_found_returns_stable_error_code(self):
        response = self.client.get("/api/documents/9999", headers=self.headers)

        self.assertEqual(response.status_code, 404)
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], "DOCUMENT_NOT_FOUND")
        self.assertEqual(payload["message"], "文档不存在")

    def test_document_visual_analyze_returns_expected_contract(self):
        self.document.file_type = "png"
        self.document.file_path = "uploads/spec.png"
        self.db.commit()

        with patch(
            "app.api.document_api.document_service.analyze_visual",
            new=AsyncMock(
                return_value={
                    "document_id": self.document.id,
                    "title": self.document.title,
                    "file_type": "png",
                    "analysis": "图片中包含签字和公章。",
                    "image_count": 1,
                }
            ),
        ):
            response = self.client.post(
                f"/api/documents/{self.document.id}/analyze-visual",
                headers=self.headers,
                json={"prompt": "请识别图中的签字和公章"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["document_id"], self.document.id)
        self.assertEqual(payload["data"]["file_type"], "png")
        self.assertIn("签字和公章", payload["data"]["analysis"])

    def test_document_visual_analyze_returns_stable_error_code_for_invalid_type(self):
        with patch(
            "app.api.document_api.document_service.analyze_visual",
            new=AsyncMock(side_effect=ValueError("Document visual analysis only supports image and PDF files")),
        ):
            response = self.client.post(
                f"/api/documents/{self.document.id}/analyze-visual",
                headers=self.headers,
                json={"prompt": "请识别图中的签字和公章"},
            )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], "DOCUMENT_VISUAL_ANALYSIS_INVALID")

    def test_document_ask_keeps_contract_when_visual_analysis_is_used(self):
        self.document.file_type = "png"
        self.document.file_path = "uploads/spec.png"
        self.db.commit()

        with patch(
            "app.api.document_api.document_service.ask",
            return_value={
                "qa_record_id": 9,
                "answer": "该页包含签字和公章。",
                "citations": [],
                "confidence": 0.83,
                "can_answer": True,
                "feedback_value": None,
                "feedback_status": None,
            },
        ):
            response = self.client.post(
                f"/api/documents/{self.document.id}/ask",
                headers=self.headers,
                json={"question": "这页有没有签字"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["qa_record_id"], 9)
        self.assertTrue(payload["data"]["can_answer"])

    def test_invalid_credentials_return_stable_error_code(self):
        response = self.client.post(
            "/api/auth/login",
            json={"username": "tester", "password": "wrong-password"},
        )

        self.assertEqual(response.status_code, 401)
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], "INVALID_CREDENTIALS")

    def test_admin_only_analytics_scope_returns_stable_error_code(self):
        response = self.client.get(
            "/api/analytics/llm-calls?scope=all",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 403)
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], "ADMIN_REQUIRED")
        self.assertEqual(payload["message"], "需要管理员权限")

    def test_llm_call_list_hides_sensitive_fields_for_non_admin(self):
        self.db.add(
            LLMCallLog(
                user_id=self.user.id,
                module_name="document",
                action="rag_answer",
                model_name="qwen-plus",
                status="error",
                error_message="upstream timeout",
                request_excerpt="secret prompt",
                response_excerpt="secret response",
            )
        )
        self.db.commit()

        response = self.client.get("/api/analytics/llm-calls", headers=self.headers)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        item = payload["data"]["items"][0]
        self.assertIsNone(item["error_message"])
        self.assertIsNone(item["request_excerpt"])
        self.assertIsNone(item["response_excerpt"])

    def test_health_check_does_not_leak_internal_error_message(self):
        class FakeDbSession:
            def execute(self, *_args, **_kwargs):
                raise RuntimeError("db password exposed")

            def close(self):
                return None

        class FakeRedisClient:
            def ping(self):
                raise RuntimeError("redis host exposed")

        class FakeHttpClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def get(self, *_args, **_kwargs):
                raise RuntimeError("llm upstream exposed")

        with patch("app.main.SessionLocal", return_value=FakeDbSession()), patch(
            "app.main.redis.from_url",
            return_value=FakeRedisClient(),
        ), patch("app.main.httpx.Client", FakeHttpClient):
            response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["status"], "degraded")
        for check in payload["data"]["checks"].values():
            self.assertEqual(set(check.keys()) & {"message", "detail", "error"}, set())

    def test_unhandled_500_does_not_leak_internal_error_detail(self):
        @app.get("/api/test-crash")
        def _test_crash():
            raise RuntimeError("db_password=super-secret")

        try:
            crash_client = TestClient(app, raise_server_exceptions=False)
            response = crash_client.get("/api/test-crash")
        finally:
            app.router.routes.pop()

        self.assertEqual(response.status_code, 500)
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], "INTERNAL_SERVER_ERROR")
        self.assertEqual(payload["error"]["detail"], "服务器内部错误")
        self.assertNotIn("super-secret", json.dumps(payload, ensure_ascii=False))

    def test_experiment_overview_requires_admin_permission(self):
        response = self.client.get(
            "/api/analytics/experiments/overview",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 403)
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], "ADMIN_REQUIRED")

    def test_document_qa_feedback_submission_returns_expected_contract(self):
        qa_record = DocumentQARecord(
            document_id=self.document.id,
            user_id=self.user.id,
            question="付款时间是什么？",
            answer="2026-07-01",
            source="document",
        )
        self.db.add(qa_record)
        self.db.commit()
        self.db.refresh(qa_record)

        response = self.client.post(
            f"/api/documents/qa-records/{qa_record.id}/feedback",
            headers=self.headers,
            json={
                "feedback_value": "negative",
                "feedback_reason": "wrong_citation",
                "feedback_note": "引用页码不对",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["id"], qa_record.id)
        self.assertEqual(payload["data"]["feedback_value"], "negative")
        self.assertEqual(payload["data"]["feedback_reason"], "wrong_citation")
        self.assertEqual(payload["data"]["feedback_status"], "open")

    def test_document_qa_feedback_requires_existing_record(self):
        response = self.client.post(
            "/api/documents/qa-records/9999/feedback",
            headers=self.headers,
            json={"feedback_value": "positive"},
        )

        self.assertEqual(response.status_code, 404)
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], "QA_RECORD_NOT_FOUND")

    def test_feedback_resolve_requires_admin_permission(self):
        qa_record = DocumentQARecord(
            document_id=self.document.id,
            user_id=self.user.id,
            question="付款时间是什么？",
            answer="2026-07-01",
            source="document",
            feedback_value="negative",
            feedback_status="open",
        )
        self.db.add(qa_record)
        self.db.commit()
        self.db.refresh(qa_record)

        response = self.client.post(
            f"/api/analytics/feedback/{qa_record.id}/resolve",
            headers=self.headers,
            json={"resolution_note": "已复盘"},
        )

        self.assertEqual(response.status_code, 403)
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], "ADMIN_REQUIRED")

    def test_admin_can_resolve_negative_feedback(self):
        admin = User(
            username="admin_feedback",
            email="admin_feedback@example.com",
            hashed_password=hash_password("secret"),
            role="admin",
        )
        self.db.add(admin)
        self.db.commit()
        self.db.refresh(admin)
        admin_token = create_access_token({"sub": admin.id})

        qa_record = DocumentQARecord(
            document_id=self.document.id,
            user_id=self.user.id,
            question="付款时间是什么？",
            answer="2026-07-01",
            source="document",
            feedback_value="negative",
            feedback_status="open",
        )
        self.db.add(qa_record)
        self.db.commit()
        self.db.refresh(qa_record)

        response = self.client.post(
            f"/api/analytics/feedback/{qa_record.id}/resolve",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"resolution_note": "已修正 Prompt"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["feedback_status"], "resolved")
        self.assertEqual(payload["data"]["feedback_resolution_note"], "已修正 Prompt")

    def test_qa_replays_returns_structured_citations(self):
        qa_record = DocumentQARecord(
            document_id=self.document.id,
            user_id=self.user.id,
            question="付款时间是什么？",
            answer="2026-07-01",
            source="document",
            citations='[{"page_number":3,"source_text":"应于2026年7月1日前支付。"}]',
            hit_chunks='[{"chunk_id":10,"content":"应于2026年7月1日前支付。"}]',
        )
        self.db.add(qa_record)
        self.db.commit()

        response = self.client.get(
            "/api/analytics/qa-replays",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["items"][0]["citations"][0]["page_number"], 3)
        self.assertEqual(payload["data"]["items"][0]["hit_chunks"][0]["chunk_id"], 10)

    def test_llm_billing_stats_returns_cost_summary(self):
        self.db.add(
            LLMCallLog(
                user_id=self.user.id,
                module_name="document",
                action="rag_answer",
                model_name="qwen-plus",
                input_tokens=1000,
                output_tokens=500,
                status="success",
            )
        )
        self.db.commit()
        response = self.client.get(
            "/api/analytics/llm-billing/stats",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertIn("summary", payload["data"])
        self.assertIn("pricing", payload["data"])

    def test_llm_routing_stats_requires_admin_and_returns_aggregate(self):
        self.db.add(
            LLMCallLog(
                user_id=self.user.id,
                module_name="chat",
                action="email_polish",
                model_name="qwen-turbo",
                status="success",
                request_id="routing-api-test",
                routing_role="small",
                routing_stage="initial",
            )
        )
        self.db.commit()

        forbidden = self.client.get("/api/analytics/llm-routing/stats", headers=self.headers)
        self.assertEqual(forbidden.status_code, 403)

        admin = User(
            username="admin_llm_routing",
            email="admin_llm_routing@example.com",
            hashed_password=hash_password("secret"),
            role="admin",
        )
        self.db.add(admin)
        self.db.commit()
        response = self.client.get(
            "/api/analytics/llm-routing/stats",
            headers={"Authorization": f"Bearer {create_access_token({'sub': admin.id})}"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["routed_requests"], 1)

    def test_llm_routing_health_requires_admin(self):
        forbidden = self.client.get("/api/analytics/llm-routing/health", headers=self.headers)
        self.assertEqual(forbidden.status_code, 403)

        admin = User(
            username="admin_llm_routing_health",
            email="admin_llm_routing_health@example.com",
            hashed_password=hash_password("secret"),
            role="admin",
        )
        self.db.add(admin)
        self.db.commit()
        response = self.client.get(
            "/api/analytics/llm-routing/health?hours=1",
            headers={"Authorization": f"Bearer {create_access_token({'sub': admin.id})}"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["status"], "ok")

    def test_database_module_supports_sqlite_connect_args(self):
        sqlite_kwargs = database_module.get_engine_kwargs("sqlite+pysqlite:///:memory:")
        mysql_kwargs = database_module.get_engine_kwargs("mysql+pymysql://root:123456@localhost:3306/aibg")

        self.assertEqual(sqlite_kwargs["connect_args"]["check_same_thread"], False)
        self.assertNotIn("pool_pre_ping", sqlite_kwargs)
        self.assertTrue(mysql_kwargs["pool_pre_ping"])

    def test_prompt_routes_require_admin_permission(self):
        response = self.client.get(
            "/api/prompts/",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 403)
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], "ADMIN_REQUIRED")
        self.assertEqual(payload["message"], "需要管理员权限")

    def test_prompt_create_returns_stable_schema_error_code(self):
        admin = User(
            username="admin_user",
            email="admin@example.com",
            hashed_password=hash_password("secret"),
            role="admin",
        )
        self.db.add(admin)
        self.db.commit()
        self.db.refresh(admin)
        admin_token = create_access_token({"sub": admin.id})

        response = self.client.post(
            "/api/prompts/",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "bad_prompt",
                "template": "Question: {question}\nContext: {context}",
                "variables": "question",
                "change_note": "init bad prompt",
            },
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], "PROMPT_VARIABLE_SCHEMA_INVALID")

    def test_admin_can_start_prompt_rollout_and_rollback(self):
        admin = User(
            username="admin_release",
            email="admin_release@example.com",
            hashed_password=hash_password("secret"),
            role="admin",
        )
        self.db.add(admin)
        self.db.commit()
        self.db.refresh(admin)
        admin_token = create_access_token({"sub": admin.id})

        tmpl = PromptTemplate(
            name="email_generate",
            description="邮件生成",
            variables="purpose",
        )
        self.db.add(tmpl)
        self.db.commit()
        self.db.refresh(tmpl)
        v1 = PromptTemplateVersion(
            template_id=tmpl.id,
            version=1,
            template="v1 -> {purpose}",
            is_active=True,
            change_note="init",
        )
        self.db.add(v1)
        self.db.commit()
        self.db.refresh(v1)
        tmpl.active_version_id = v1.id
        self.db.add(tmpl)
        self.db.commit()
        v2 = PromptTemplateVersion(
            template_id=tmpl.id,
            version=2,
            template="v2 -> {purpose}",
            is_active=False,
            change_note="candidate",
        )
        self.db.add(v2)
        self.db.commit()
        self.db.refresh(v2)

        rollout_response = self.client.post(
            f"/api/prompts/{tmpl.id}/rollout",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"version_id": v2.id, "rollout_percentage": 25},
        )

        self.assertEqual(rollout_response.status_code, 200)
        rollout_payload = rollout_response.json()
        self.assertTrue(rollout_payload["success"])
        self.assertEqual(rollout_payload["data"]["rollout"]["version_id"], v2.id)
        self.assertEqual(rollout_payload["data"]["rollout"]["percentage"], 25)

        rollback_response = self.client.post(
            f"/api/prompts/{tmpl.id}/rollback",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={},
        )

        self.assertEqual(rollback_response.status_code, 200)
        rollback_payload = rollback_response.json()
        self.assertTrue(rollback_payload["success"])
        self.assertIsNone(rollback_payload["data"]["rollout"])

    def test_prompt_rollback_returns_stable_error_when_unavailable(self):
        admin = User(
            username="admin_no_rollback",
            email="admin_no_rollback@example.com",
            hashed_password=hash_password("secret"),
            role="admin",
        )
        self.db.add(admin)
        self.db.commit()
        self.db.refresh(admin)
        admin_token = create_access_token({"sub": admin.id})

        tmpl = PromptTemplate(
            name="meeting_summary",
            description="会议总结",
            variables="meeting_content",
        )
        self.db.add(tmpl)
        self.db.commit()
        self.db.refresh(tmpl)
        v1 = PromptTemplateVersion(
            template_id=tmpl.id,
            version=1,
            template="v1 -> {meeting_content}",
            is_active=True,
            change_note="init",
        )
        self.db.add(v1)
        self.db.commit()
        self.db.refresh(v1)
        tmpl.active_version_id = v1.id
        self.db.add(tmpl)
        self.db.commit()

        response = self.client.post(
            f"/api/prompts/{tmpl.id}/rollback",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={},
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], "PROMPT_ROLLBACK_NOT_AVAILABLE")

    def test_meeting_async_submit_returns_expected_contract(self):
        fake_task = type("FakeTask", (), {"id": "meeting-task-001"})()

        with patch("app.tasks.summarize_meeting_task.delay", return_value=fake_task):
            response = self.client.post(
                f"/api/meetings/{self.meeting.id}/summarize",
                headers=self.headers,
                json={"async_mode": True},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["meeting_id"], self.meeting.id)
        self.assertEqual(payload["data"]["task_id"], "meeting-task-001")
        self.assertEqual(payload["data"]["state"], "PENDING")
        self.assertTrue(payload["data"]["async_mode"])

    def test_meeting_image_upload_returns_expected_contract(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "app.services.meeting_service.UPLOAD_DIR",
            Path(tmpdir),
        ), patch(
            "app.services.meeting_service.extract_file_text",
            return_value="会议纪要截图内容：王敏负责周报，李雷负责客户同步。",
        ):
            response = self.client.post(
                "/api/meetings/upload-image",
                headers=self.headers,
                data={"title": "截图纪要"},
                files={"file": ("meeting.png", b"fake-image-bytes", "image/png")},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["title"], "截图纪要")
        self.assertEqual(payload["data"]["status"], "pending")
        self.assertIn("王敏负责周报", payload["data"]["transcript"])

    def test_meeting_audio_upload_returns_expected_contract(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "app.services.meeting_service.UPLOAD_DIR",
            Path(tmpdir),
        ):
            response = self.client.post(
                "/api/meetings/upload-audio",
                headers=self.headers,
                data={"title": "音频会议", "transcript_text": "李雷负责周五前同步客户最新排期。"},
                files={"file": ("meeting.mp3", b"fake-audio-bytes", "audio/mpeg")},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["title"], "音频会议")
        self.assertIn("李雷负责周五前同步客户最新排期", payload["data"]["transcript"])

    def test_mcp_agent_types_returns_expected_contract(self):
        response = self.client.get(
            "/api/mcp/agent-types",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertGreaterEqual(payload["data"]["total"], 1)
        self.assertIn("agent_type", payload["data"]["items"][0])
        self.assertIn("allowed_tools", payload["data"]["items"][0])

    def test_agent_registry_returns_only_canonical_role_contracts(self):
        response = self.client.get("/api/agent/registry", headers=self.headers)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["registry_version"], "enterprise_experts_v1")
        self.assertEqual(payload["data"]["task_protocol_version"], "agent_task_v1")
        self.assertEqual(
            [item["agent_type"] for item in payload["data"]["items"]],
            ["knowledge_agent", "meeting_agent", "data_agent", "project_agent", "legal_compliance_agent", "communication_agent", "workflow_agent"],
        )
        self.assertEqual(payload["data"]["supervisor"]["agent_type"], "supervisor_agent")

    def test_mcp_tools_list_returns_filtered_specs(self):
        fake_tools = {
            "task_query_tool": FakeTool("task_query_tool", "查询任务"),
            "email_writer_tool": FakeTool("email_writer_tool", "写邮件"),
        }

        with patch.dict("app.mcp.registry._TOOL_INSTANCES", fake_tools, clear=True):
            response = self.client.get(
                "/api/mcp/tools",
                headers=self.headers,
                params={"agent_type": "task_email_agent"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["agent_type"], "task_email_agent")
        self.assertEqual([item["name"] for item in payload["data"]["items"]], ["email_writer_tool", "task_query_tool"])

    def test_mcp_tool_call_returns_expected_contract(self):
        fake_tools = {
            "task_query_tool": FakeTool(
                "task_query_tool",
                "查询任务",
                auto_context_fields=("user_id",),
                parameters={
                    "type": "object",
                    "properties": {
                        "status": {"type": "string"},
                        "user_id": {"type": "integer"},
                    },
                    "required": ["status", "user_id"],
                },
                handler=lambda **kwargs: tool_success(
                    "查询成功",
                    {"status": kwargs["status"], "user_id": kwargs["user_id"]},
                ),
            )
        }

        with patch.dict("app.mcp.registry._TOOL_INSTANCES", fake_tools, clear=True):
            response = self.client.post(
                "/api/mcp/tools/call",
                headers=self.headers,
                json={
                    "tool_name": "task_query_tool",
                    "agent_type": "task_agent",
                    "arguments": {"status": "todo"},
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["tool_name"], "task_query_tool")
        self.assertEqual(payload["data"]["agent_type"], "task_agent")
        self.assertTrue(payload["data"]["result"]["success"])
        self.assertEqual(payload["data"]["result"]["data"]["status"], "todo")
        self.assertEqual(payload["data"]["result"]["data"]["user_id"], self.user.id)

    def test_mcp_tool_call_hides_tool_error_detail(self):
        fake_tools = {
            "task_query_tool": FakeTool(
                "task_query_tool",
                "查询任务",
                auto_context_fields=("user_id",),
                parameters={
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "integer"},
                    },
                    "required": ["user_id"],
                },
                handler=lambda **kwargs: {
                    "success": False,
                    "message": "任务查询失败",
                    "data": {"user_id": kwargs["user_id"]},
                    "error": "db_password=secret",
                },
            )
        }

        with patch.dict("app.mcp.registry._TOOL_INSTANCES", fake_tools, clear=True):
            response = self.client.post(
                "/api/mcp/tools/call",
                headers=self.headers,
                json={
                    "tool_name": "task_query_tool",
                    "agent_type": "task_agent",
                    "arguments": {},
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["data"]["result"]["success"])
        self.assertEqual(payload["data"]["result"]["message"], "任务查询失败")
        self.assertEqual(payload["data"]["result"]["error"], "任务查询失败")
        self.assertNotIn("secret", json.dumps(payload, ensure_ascii=False))

    def test_mcp_tool_call_rejects_sql_tool_for_non_admin(self):
        fake_tools = {
            "sql_query_tool": FakeTool("sql_query_tool", "执行 SQL"),
        }

        with patch.dict("app.mcp.registry._TOOL_INSTANCES", fake_tools, clear=True):
            response = self.client.post(
                "/api/mcp/tools/call",
                headers=self.headers,
                json={
                    "tool_name": "sql_query_tool",
                    "agent_type": "document_agent",
                    "arguments": {},
                },
            )

        self.assertEqual(response.status_code, 403)
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], "ADMIN_REQUIRED")

    def test_document_batch_upload_supports_governance_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "app.services.document_service.UPLOAD_DIR",
            Path(tmpdir),
        ), patch(
            "app.services.document_service._extract_segments",
            return_value=[{"text": "付款条款", "page_number": 1, "section_title": "正文"}],
        ), patch(
            "app.services.document_service._split_text",
            return_value=[{"chunk_index": 0, "content": "付款条款", "page_number": 1, "section_title": "正文"}],
        ), patch(
            "app.services.document_service._try_index_document",
            return_value=None,
        ):
            response = self.client.post(
                "/api/documents/batch-upload?async_mode=false",
                headers=self.headers,
                data={
                    "knowledge_base_name": "合同库",
                    "knowledge_base_category": "contract",
                    "classification": "legal",
                    "tags": "合同,付款",
                    "permission_scope": "restricted",
                    "permission_users": str(self.user.id),
                    "permission_roles": "admin",
                },
                files=[
                    ("files", ("a.md", b"alpha", "text/markdown")),
                    ("files", ("b.md", b"beta", "text/markdown")),
                ],
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["count"], 2)
        self.assertEqual(payload["data"]["documents"][0]["knowledge_base_id"], 1)
        self.assertEqual(payload["data"]["documents"][0]["classification"], "legal")
        self.assertEqual(payload["data"]["documents"][0]["permission_scope"], "restricted")

    def test_document_versions_returns_previous_versions(self):
        root = Document(
            user_id=self.user.id,
            title="合同.md",
            file_path="uploads/root.md",
            file_type="md",
            status="indexed",
            version_number=1,
            content_hash="hash-v1",
        )
        self.db.add(root)
        self.db.commit()
        self.db.refresh(root)
        v2 = Document(
            user_id=self.user.id,
            title="合同.md",
            file_path="uploads/v2.md",
            file_type="md",
            status="indexed",
            parent_document_id=root.id,
            version_number=2,
            content_hash="hash-v2",
        )
        self.db.add(v2)
        self.db.commit()

        response = self.client.get(f"/api/documents/{root.id}/versions", headers=self.headers)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["total"], 2)
        self.assertEqual(payload["data"]["items"][0]["version_number"], 2)

    def test_mcp_tool_call_returns_approval_required_for_high_risk_tool(self):
        fake_tools = {
            "task_create_tool": FakeTool(
                "task_create_tool",
                "创建任务",
                auto_context_fields=("user_id",),
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "user_id": {"type": "integer"},
                    },
                    "required": ["title", "user_id"],
                },
            )
        }

        with patch.dict("app.mcp.registry._TOOL_INSTANCES", fake_tools, clear=True):
            response = self.client.post(
                "/api/mcp/tools/call",
                headers=self.headers,
                json={
                    "tool_name": "task_create_tool",
                    "agent_type": "task_agent",
                    "arguments": {"title": "审批测试任务"},
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["data"]["result"]["success"])
        self.assertEqual(payload["data"]["result"]["mcp_error_code"], "MCP_APPROVAL_REQUIRED")
        self.assertTrue(payload["data"]["result"]["data"]["approval_required"])

    def test_agent_approval_decision_endpoint_updates_status(self):
        from app.models.agent import AgentApprovalRequest

        approval = AgentApprovalRequest(
            user_id=self.user.id,
            tool_name="task_create_tool",
            agent_type="task_agent",
            approval_token="token-1",
            status="pending",
            risk_level="high",
        )
        self.db.add(approval)
        self.db.commit()
        self.db.refresh(approval)

        response = self.client.post(
            f"/api/agent/approvals/{approval.id}/decision",
            headers=self.headers,
            json={"approved": True, "decision_note": "允许执行"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["status"], "approved")
        self.assertEqual(payload["data"]["decision_note"], "允许执行")

    def test_generate_email_from_tasks_returns_draft(self):
        overdue_task = Task(
            user_id=self.user.id,
            title="提交周报",
            status="todo",
            priority="high",
        )
        active_task = Task(
            user_id=self.user.id,
            title="同步客户排期",
            status="in_progress",
            priority="medium",
            assignee="李雷",
        )
        done_task = Task(
            user_id=self.user.id,
            title="已完成事项",
            status="done",
            priority="low",
        )
        self.db.add_all([overdue_task, active_task, done_task])
        self.db.commit()

        async def fake_generate_email(recipient, purpose, key_points, tone, need_action, user_id=None):
            self.assertEqual(purpose, "任务进度同步")
            self.assertEqual(tone, "professional")
            self.assertTrue(need_action)
            self.assertEqual(user_id, self.user.id)
            self.assertEqual(len(key_points), 2)
            self.assertIn("提交周报", key_points[0])
            self.assertTrue(any("同步客户排期" in item for item in key_points))
            return ["任务进度同步", "项目任务同步"], "请查收当前任务进度。"

        with patch("app.services.email_service.email_ai_service.generate_email", new=AsyncMock(side_effect=fake_generate_email)):
            response = self.client.post(
                "/api/emails/from-tasks",
                headers=self.headers,
                json={"purpose": "任务进度同步", "tone": "professional", "need_action": True},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["draft"]["purpose"], "任务进度同步")
        self.assertEqual(payload["data"]["draft"]["generation_type"], "task_sync")
        self.assertEqual(len(payload["data"]["draft"]["key_points"]), 2)
        self.assertEqual(payload["data"]["subject_candidates"][0], "任务进度同步")
        self.assertIn('"source_type": "task_sync"', payload["data"]["draft"]["metadata_json"])

        db = self.TestingSessionLocal()
        try:
            drafts = db.query(EmailDraft).filter_by(user_id=self.user.id).all()
            self.assertEqual(len(drafts), 1)
            self.assertEqual(drafts[0].purpose, "任务进度同步")
        finally:
            db.close()

    def test_generate_email_from_tasks_returns_stable_empty_error(self):
        response = self.client.post(
            "/api/emails/from-tasks",
            headers=self.headers,
            json={"purpose": "任务进度同步"},
        )

        self.assertEqual(response.status_code, 404)
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], "TASK_SYNC_SOURCE_EMPTY")

    def test_generate_email_from_tasks_supports_shared_scope(self):
        self.user.organization_id = 901
        self.user.department_id = 902
        self.db.add(self.user)
        self.db.commit()

        peer = User(
            username="peer_task_sync_scope",
            email="peer_task_sync_scope@example.com",
            hashed_password=hash_password("secret"),
            organization_id=901,
            department_id=902,
        )
        self.db.add(peer)
        self.db.commit()
        self.db.refresh(peer)

        shared_task = Task(
            user_id=peer.id,
            organization_id=901,
            department_id=902,
            title="部门共享催办",
            status="todo",
            priority="high",
        )
        self.db.add(shared_task)
        self.db.commit()

        async def fake_generate_email(recipient, purpose, key_points, tone, need_action, user_id=None):
            self.assertEqual(purpose, "任务进度同步")
            self.assertEqual(user_id, self.user.id)
            self.assertTrue(any("部门共享催办" in item for item in key_points))
            return ["部门共享任务同步"], "请同步处理共享任务。"

        with patch("app.services.email_service.email_ai_service.generate_email", new=AsyncMock(side_effect=fake_generate_email)):
            response = self.client.post(
                "/api/emails/from-tasks",
                headers=self.headers,
                json={"purpose": "任务进度同步", "scope": "department"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["draft"]["purpose"], "任务进度同步")
        self.assertTrue(any("部门共享催办" in item for item in payload["data"]["draft"]["key_points"]))
        self.assertIn('"source_type": "task_sync"', payload["data"]["draft"]["metadata_json"])

    def test_task_comments_and_logs_contract(self):
        task = Task(
            user_id=self.user.id,
            title="跟进合同",
            status="todo",
            priority="medium",
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)

        comment_response = self.client.post(
            f"/api/tasks/{task.id}/comments",
            headers=self.headers,
            json={"content": "已联系法务同事协助审阅"},
        )
        update_response = self.client.put(
            f"/api/tasks/{task.id}",
            headers=self.headers,
            json={"progress": 40, "collaborators": ["法务", "采购"]},
        )
        comments_response = self.client.get(f"/api/tasks/{task.id}/comments", headers=self.headers)
        logs_response = self.client.get(f"/api/tasks/{task.id}/logs", headers=self.headers)

        self.assertEqual(comment_response.status_code, 200)
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(comments_response.status_code, 200)
        self.assertEqual(logs_response.status_code, 200)
        self.assertEqual(update_response.json()["data"]["progress"], 40)
        self.assertEqual(update_response.json()["data"]["collaborators"], ["法务", "采购"])
        self.assertEqual(comments_response.json()["data"][0]["content"], "已联系法务同事协助审阅")
        self.assertTrue(any(item["action"] == "comment_added" for item in logs_response.json()["data"]))

    def test_email_thread_reply_returns_draft(self):
        async def fake_reply_from_thread(**kwargs):
            draft = EmailDraft(
                user_id=self.user.id,
                subject="Re: 项目排期同步",
                recipient=kwargs.get("recipient"),
                content="已收到，我们会在周五前同步最新排期。",
                purpose="回复来信",
                tone=kwargs.get("tone", "professional"),
                generation_type="reply",
                reply_goal=kwargs.get("reply_goal"),
                status="draft",
            )
            self.db.add(draft)
            self.db.commit()
            self.db.refresh(draft)
            return {"draft": draft, "subject_candidates": ["Re: 项目排期同步"], "thread_summary": {"summary": "客户催进度"}}

        with patch("app.api.email_api.email_service.reply_from_thread", new=AsyncMock(side_effect=fake_reply_from_thread)):
            response = self.client.post(
                "/api/emails/thread-reply",
                headers=self.headers,
                json={
                    "emails": ["邮件1", "邮件2"],
                    "reply_goal": "确认周五前给出排期",
                    "tone": "professional",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["draft"]["generation_type"], "reply")
        self.assertEqual(payload["data"]["draft"]["reply_goal"], "确认周五前给出排期")

    def test_tool_health_and_feedback_bundle_contract(self):
        admin = User(
            username="admin_tool_health",
            email="admin_tool_health@example.com",
            hashed_password=hash_password("secret"),
            role="admin",
        )
        self.db.add(admin)
        self.db.commit()
        self.db.refresh(admin)
        admin_token = create_access_token({"sub": admin.id})

        qa_record = DocumentQARecord(
            document_id=self.document.id,
            user_id=self.user.id,
            question="付款条件是什么？",
            answer="不知道",
            source="document",
            feedback_value="negative",
            feedback_status="open",
            feedback_reason="incorrect_answer",
            feedback_created_at=datetime.utcnow(),
        )
        self.db.add(qa_record)
        agent_run = AgentRun(user_id=self.user.id, goal="创建任务", status="completed")
        self.db.add(agent_run)
        self.db.commit()
        self.db.refresh(agent_run)
        self.db.add(
            ToolCallLog(
                agent_run_id=agent_run.id,
                step=1,
                tool_name="task_create_tool",
                status="pending_approval",
                error="工具调用需要人工审批",
                duration_ms=10,
            )
        )
        self.db.commit()

        tool_health_response = self.client.get("/api/analytics/tool-health", headers=self.headers)
        bundle_response = self.client.post(
            "/api/analytics/feedback/export-eval-bundle",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"days": 30},
        )

        self.assertEqual(tool_health_response.status_code, 200)
        self.assertEqual(bundle_response.status_code, 200)
        self.assertTrue(tool_health_response.json()["success"])
        self.assertEqual(tool_health_response.json()["data"]["items"][0]["tool_name"], "task_create_tool")
        self.assertTrue(bundle_response.json()["success"])
        self.assertGreaterEqual(bundle_response.json()["data"]["count"], 1)

    def test_org_and_department_management_contract(self):
        admin = User(
            username="admin_org",
            email="admin_org@example.com",
            hashed_password=hash_password("secret"),
            role="admin",
        )
        self.db.add(admin)
        self.db.commit()
        self.db.refresh(admin)
        admin_token = create_access_token({"sub": admin.id})

        org_response = self.client.post(
            "/api/org/organizations",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "OpenAI CN", "code": "OA-CN", "description": "China org"},
        )
        self.assertEqual(org_response.status_code, 200)
        org_id = org_response.json()["data"]["id"]

        dept_response = self.client.post(
            "/api/org/departments",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"organization_id": org_id, "name": "Legal", "code": "LEGAL"},
        )
        self.assertEqual(dept_response.status_code, 200)
        dept_id = dept_response.json()["data"]["id"]

        assign_response = self.client.post(
            f"/api/org/users/{self.user.id}/assign",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"organization_id": org_id, "department_id": dept_id, "job_title": "PM"},
        )
        self.assertEqual(assign_response.status_code, 200)
        self.assertEqual(assign_response.json()["data"]["organization_id"], org_id)
        self.assertEqual(assign_response.json()["data"]["department_id"], dept_id)

        users_response = self.client.get(
            "/api/auth/users",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        self.assertEqual(users_response.status_code, 200)
        self.assertTrue(users_response.json()["success"])
        self.assertTrue(any(item["id"] == self.user.id for item in users_response.json()["data"]))

    def test_task_meeting_email_inherit_user_org_scope(self):
        self.user.organization_id = 101
        self.user.department_id = 202
        self.db.add(self.user)
        self.db.commit()

        task_response = self.client.post(
            "/api/tasks/",
            headers=self.headers,
            json={"title": "跟进合同评审"},
        )
        self.assertEqual(task_response.status_code, 200)
        self.assertEqual(task_response.json()["data"]["organization_id"], 101)
        self.assertEqual(task_response.json()["data"]["department_id"], 202)

        meeting_response = self.client.post(
            "/api/meetings/",
            headers=self.headers,
            json={"title": "法务评审会", "transcript": "法务确认条款调整。"},
        )
        self.assertEqual(meeting_response.status_code, 200)
        self.assertEqual(meeting_response.json()["data"]["organization_id"], 101)
        self.assertEqual(meeting_response.json()["data"]["department_id"], 202)

        async def fake_generate_email(recipient, purpose, key_points, tone, need_action, user_id=None):
            return ["合同同步"], "请查看合同进展。"

        with patch("app.services.email_service.email_ai_service.generate_email", new=AsyncMock(side_effect=fake_generate_email)):
            email_response = self.client.post(
                "/api/emails/generate",
                headers=self.headers,
                json={"purpose": "合同进度同步", "recipient": "legal@example.com"},
            )

        self.assertEqual(email_response.status_code, 200)
        self.assertEqual(email_response.json()["data"]["draft"]["organization_id"], 101)
        self.assertEqual(email_response.json()["data"]["draft"]["department_id"], 202)

    def test_task_meeting_email_scope_allows_same_department_read_only(self):
        self.user.organization_id = 301
        self.user.department_id = 401
        self.db.add(self.user)
        self.db.commit()

        peer = User(
            username="peer_scope",
            email="peer_scope@example.com",
            hashed_password=hash_password("secret"),
            organization_id=301,
            department_id=401,
        )
        outsider = User(
            username="outsider_scope",
            email="outsider_scope@example.com",
            hashed_password=hash_password("secret"),
            organization_id=999,
            department_id=888,
        )
        self.db.add(peer)
        self.db.add(outsider)
        self.db.commit()
        self.db.refresh(peer)
        self.db.refresh(outsider)
        peer_headers = {"Authorization": f"Bearer {create_access_token({'sub': peer.id})}"}
        outsider_headers = {"Authorization": f"Bearer {create_access_token({'sub': outsider.id})}"}

        task_response = self.client.post(
            "/api/tasks/",
            headers=self.headers,
            json={"title": "部门共享任务"},
        )
        meeting_response = self.client.post(
            "/api/meetings/",
            headers=self.headers,
            json={"title": "部门周会", "transcript": "同步合同进展"},
        )

        async def fake_generate_email(recipient, purpose, key_points, tone, need_action, user_id=None):
            return ["部门同步"], "请同步处理。"

        with patch("app.services.email_service.email_ai_service.generate_email", new=AsyncMock(side_effect=fake_generate_email)):
            email_response = self.client.post(
                "/api/emails/generate",
                headers=self.headers,
                json={"purpose": "部门同步"},
            )

        task_id = task_response.json()["data"]["id"]
        meeting_id = meeting_response.json()["data"]["id"]
        draft_id = email_response.json()["data"]["draft"]["id"]

        same_dept_task_list = self.client.get("/api/tasks/?page=1&page_size=20", headers=peer_headers)
        same_dept_meeting_list = self.client.get("/api/meetings/?page=1&page_size=20", headers=peer_headers)
        same_dept_email_list = self.client.get("/api/emails/?page=1&page_size=20", headers=peer_headers)
        same_dept_task_get = self.client.get(f"/api/tasks/{task_id}", headers=peer_headers)
        same_dept_meeting_get = self.client.get(f"/api/meetings/{meeting_id}", headers=peer_headers)
        same_dept_email_get = self.client.get(f"/api/emails/{draft_id}", headers=peer_headers)

        self.assertEqual(same_dept_task_list.status_code, 200)
        self.assertTrue(any(item["id"] == task_id for item in same_dept_task_list.json()["data"]["items"]))
        self.assertEqual(same_dept_meeting_list.status_code, 200)
        self.assertTrue(any(item["id"] == meeting_id for item in same_dept_meeting_list.json()["data"]["items"]))
        self.assertEqual(same_dept_email_list.status_code, 200)
        self.assertTrue(any(item["id"] == draft_id for item in same_dept_email_list.json()["data"]["items"]))
        self.assertEqual(same_dept_task_get.status_code, 200)
        self.assertEqual(same_dept_meeting_get.status_code, 200)
        self.assertEqual(same_dept_email_get.status_code, 200)

        with patch("app.services.meeting_service.analysis_service.summarize_meeting", new=AsyncMock(return_value={
            "theme": "部门周会",
            "summary": "同步合同进展",
            "topics": [],
            "decisions": [],
            "action_items": [],
            "risks": [],
        })):
            summarize_response = self.client.post(
                f"/api/meetings/{meeting_id}/summarize",
                headers=self.headers,
                json={"async_mode": False},
            )

        self.assertEqual(summarize_response.status_code, 200)
        same_dept_summary_get = self.client.get(f"/api/meetings/{meeting_id}/summary", headers=peer_headers)
        self.assertEqual(same_dept_summary_get.status_code, 200)
        self.assertEqual(same_dept_summary_get.json()["data"]["theme"], "部门周会")

        outsider_task_get = self.client.get(f"/api/tasks/{task_id}", headers=outsider_headers)
        outsider_meeting_get = self.client.get(f"/api/meetings/{meeting_id}", headers=outsider_headers)
        outsider_email_get = self.client.get(f"/api/emails/{draft_id}", headers=outsider_headers)
        outsider_summary_get = self.client.get(f"/api/meetings/{meeting_id}/summary", headers=outsider_headers)

        self.assertEqual(outsider_task_get.status_code, 404)
        self.assertEqual(outsider_meeting_get.status_code, 404)
        self.assertEqual(outsider_email_get.status_code, 404)
        self.assertEqual(outsider_summary_get.status_code, 404)

    def test_task_meeting_email_scope_filters_split_mine_department_and_organization(self):
        self.user.organization_id = 701
        self.user.department_id = 801
        self.db.add(self.user)
        self.db.commit()

        peer = User(
            username="peer_scope_filter",
            email="peer_scope_filter@example.com",
            hashed_password=hash_password("secret"),
            organization_id=701,
            department_id=801,
        )
        orgmate = User(
            username="org_scope_filter",
            email="org_scope_filter@example.com",
            hashed_password=hash_password("secret"),
            organization_id=701,
            department_id=999,
        )
        self.db.add(peer)
        self.db.add(orgmate)
        self.db.commit()
        self.db.refresh(peer)
        self.db.refresh(orgmate)

        peer_headers = {"Authorization": f"Bearer {create_access_token({'sub': peer.id})}"}
        orgmate_headers = {"Authorization": f"Bearer {create_access_token({'sub': orgmate.id})}"}

        own_task = self.client.post("/api/tasks/", headers=self.headers, json={"title": "我的任务"}).json()["data"]
        dept_task = self.client.post("/api/tasks/", headers=peer_headers, json={"title": "部门任务"}).json()["data"]
        org_task = self.client.post("/api/tasks/", headers=orgmate_headers, json={"title": "组织任务"}).json()["data"]

        own_meeting = self.client.post(
            "/api/meetings/",
            headers=self.headers,
            json={"title": "我的会议", "transcript": "个人会议纪要"},
        ).json()["data"]
        dept_meeting = self.client.post(
            "/api/meetings/",
            headers=peer_headers,
            json={"title": "部门会议", "transcript": "部门会议纪要"},
        ).json()["data"]
        org_meeting = self.client.post(
            "/api/meetings/",
            headers=orgmate_headers,
            json={"title": "组织会议", "transcript": "组织会议纪要"},
        ).json()["data"]

        async def fake_generate_email(recipient, purpose, key_points, tone, need_action, user_id=None):
            return [purpose], f"{purpose}正文"

        with patch("app.services.email_service.email_ai_service.generate_email", new=AsyncMock(side_effect=fake_generate_email)):
            own_draft = self.client.post("/api/emails/generate", headers=self.headers, json={"purpose": "我的邮件"}).json()["data"]["draft"]
            dept_draft = self.client.post("/api/emails/generate", headers=peer_headers, json={"purpose": "部门邮件"}).json()["data"]["draft"]
            org_draft = self.client.post("/api/emails/generate", headers=orgmate_headers, json={"purpose": "组织邮件"}).json()["data"]["draft"]

        mine_tasks = self.client.get("/api/tasks/?scope=mine&page=1&page_size=20", headers=self.headers)
        dept_tasks = self.client.get("/api/tasks/?scope=department&page=1&page_size=20", headers=self.headers)
        org_tasks = self.client.get("/api/tasks/?scope=organization&page=1&page_size=20", headers=self.headers)

        mine_task_ids = {item["id"] for item in mine_tasks.json()["data"]["items"]}
        dept_task_ids = {item["id"] for item in dept_tasks.json()["data"]["items"]}
        org_task_ids = {item["id"] for item in org_tasks.json()["data"]["items"]}

        self.assertIn(own_task["id"], mine_task_ids)
        self.assertNotIn(dept_task["id"], mine_task_ids)
        self.assertNotIn(org_task["id"], mine_task_ids)
        self.assertIn(dept_task["id"], dept_task_ids)
        self.assertNotIn(own_task["id"], dept_task_ids)
        self.assertNotIn(org_task["id"], dept_task_ids)
        self.assertIn(org_task["id"], org_task_ids)
        self.assertNotIn(own_task["id"], org_task_ids)
        self.assertNotIn(dept_task["id"], org_task_ids)

        mine_meetings = self.client.get("/api/meetings/?scope=mine&page=1&page_size=20", headers=self.headers)
        dept_meetings = self.client.get("/api/meetings/?scope=department&page=1&page_size=20", headers=self.headers)
        org_meetings = self.client.get("/api/meetings/?scope=organization&page=1&page_size=20", headers=self.headers)

        mine_meeting_ids = {item["id"] for item in mine_meetings.json()["data"]["items"]}
        dept_meeting_ids = {item["id"] for item in dept_meetings.json()["data"]["items"]}
        org_meeting_ids = {item["id"] for item in org_meetings.json()["data"]["items"]}

        self.assertIn(own_meeting["id"], mine_meeting_ids)
        self.assertNotIn(dept_meeting["id"], mine_meeting_ids)
        self.assertNotIn(org_meeting["id"], mine_meeting_ids)
        self.assertIn(dept_meeting["id"], dept_meeting_ids)
        self.assertNotIn(own_meeting["id"], dept_meeting_ids)
        self.assertNotIn(org_meeting["id"], dept_meeting_ids)
        self.assertIn(org_meeting["id"], org_meeting_ids)
        self.assertNotIn(own_meeting["id"], org_meeting_ids)
        self.assertNotIn(dept_meeting["id"], org_meeting_ids)

        mine_emails = self.client.get("/api/emails/?scope=mine&page=1&page_size=20", headers=self.headers)
        dept_emails = self.client.get("/api/emails/?scope=department&page=1&page_size=20", headers=self.headers)
        org_emails = self.client.get("/api/emails/?scope=organization&page=1&page_size=20", headers=self.headers)

        mine_email_ids = {item["id"] for item in mine_emails.json()["data"]["items"]}
        dept_email_ids = {item["id"] for item in dept_emails.json()["data"]["items"]}
        org_email_ids = {item["id"] for item in org_emails.json()["data"]["items"]}

        self.assertIn(own_draft["id"], mine_email_ids)
        self.assertNotIn(dept_draft["id"], mine_email_ids)
        self.assertNotIn(org_draft["id"], mine_email_ids)
        self.assertIn(dept_draft["id"], dept_email_ids)
        self.assertNotIn(own_draft["id"], dept_email_ids)
        self.assertNotIn(org_draft["id"], dept_email_ids)
        self.assertIn(org_draft["id"], org_email_ids)
        self.assertNotIn(own_draft["id"], org_email_ids)
        self.assertNotIn(dept_draft["id"], org_email_ids)

    def test_task_list_supports_source_filters(self):
        document_task = Task(
            user_id=self.user.id,
            title="文档任务",
            status="todo",
            priority="medium",
            source_type="document",
            source_id=11,
        )
        meeting_task = Task(
            user_id=self.user.id,
            title="会议任务",
            status="todo",
            priority="medium",
            source_type="meeting",
            source_id=22,
        )
        self.db.add_all([document_task, meeting_task])
        self.db.commit()

        response = self.client.get(
            "/api/tasks/",
            headers=self.headers,
            params={"source_type": "document", "source_id": 11, "page": 1, "page_size": 20},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        ids = [item["id"] for item in payload["data"]["items"]]
        self.assertIn(document_task.id, ids)
        self.assertNotIn(meeting_task.id, ids)

    def test_agent_run_list_supports_artifact_filters(self):
        document_run = AgentRun(
            user_id=self.user.id,
            goal="分析文档风险",
            status="completed",
            result=json.dumps({"artifacts": {"documents": [{"document_id": 101, "summary": "合同风险"}]}}, ensure_ascii=False),
            final_answer="已完成文档分析",
            total_steps=2,
        )
        task_run = AgentRun(
            user_id=self.user.id,
            goal="创建任务",
            status="completed",
            result=json.dumps({"artifacts": {"tasks": [{"task_id": 202, "title": "跟进合同"}]}}, ensure_ascii=False),
            final_answer="已创建任务",
            total_steps=1,
        )
        self.db.add_all([document_run, task_run])
        self.db.commit()

        response = self.client.get(
            "/api/agent/runs",
            headers=self.headers,
            params={"artifact_type": "document", "artifact_id": 101, "page": 1, "page_size": 20},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        ids = [item["id"] for item in payload["data"]["items"]]
        self.assertIn(document_run.id, ids)
        self.assertNotIn(task_run.id, ids)

    def test_sensitive_document_filter_contract(self):
        sensitive_doc = Document(
            user_id=self.user.id,
            title="confidential-plan.md",
            file_path="uploads/confidential-plan.md",
            file_type="md",
            status="indexed",
            sensitivity_level="confidential",
        )
        self.db.add(sensitive_doc)
        self.db.commit()

        response = self.client.get(
            "/api/documents/",
            headers=self.headers,
            params={"sensitivity_level": "confidential"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["items"][0]["sensitivity_level"], "confidential")

    def test_document_connector_filter_contract(self):
        connector_doc = Document(
            user_id=self.user.id,
            title="connector-plan.md",
            file_path="uploads/connector-plan.md",
            file_type="md",
            status="indexed",
            metadata_json=json.dumps({"connector_id": 9, "connector_name": "Shared Drive"}, ensure_ascii=False),
        )
        normal_doc = Document(
            user_id=self.user.id,
            title="manual-note.md",
            file_path="uploads/manual-note.md",
            file_type="md",
            status="indexed",
        )
        self.db.add(connector_doc)
        self.db.add(normal_doc)
        self.db.commit()

        response = self.client.get(
            "/api/documents/",
            headers=self.headers,
            params={"connector_id": 9},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(len(payload["data"]["items"]), 1)
        self.assertEqual(payload["data"]["items"][0]["connector_id"], 9)
        self.assertEqual(payload["data"]["items"][0]["connector_name"], "Shared Drive")

    def test_connector_contract(self):
        response = self.client.post(
            "/api/connectors/",
            headers=self.headers,
            json={"connector_type": "drive", "name": "Shared Drive", "config_json": '{"path":"contracts"}'},
        )
        self.assertEqual(response.status_code, 200)
        connector_id = response.json()["data"]["id"]

        fake_task = type("Task", (), {"id": "connector-task-1"})()
        with patch("app.tasks.connector_sync_task.delay", return_value=fake_task):
            sync_response = self.client.post(
                f"/api/connectors/{connector_id}/sync",
                headers=self.headers,
                json={"sync_mode": "manual"},
            )
        list_response = self.client.get("/api/connectors/", headers=self.headers)
        jobs_response = self.client.get("/api/connectors/sync-jobs", headers=self.headers)
        sync_job_id = jobs_response.json()["data"][0]["id"]
        self.db.add(
            OperationLog(
                user_id=self.user.id,
                module="async_task",
                action="connector_sync_submitted",
                target_type="connector_sync_job",
                target_id=sync_job_id,
                detail="task_id=connector-task-1; connector_id=1; sync_mode=manual",
            )
        )
        self.db.commit()
        fake_async_result = type(
            "AsyncResult",
            (),
            {
                "state": "PENDING",
                "failed": lambda self: False,
                "successful": lambda self: False,
                "info": None,
            },
        )()
        with patch("app.services.analytics_service.celery_app.AsyncResult", return_value=fake_async_result):
            task_runs_response = self.client.get("/api/analytics/task-runs", headers=self.headers)

        self.assertEqual(sync_response.status_code, 200)
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(jobs_response.status_code, 200)
        self.assertEqual(task_runs_response.status_code, 200)
        self.assertEqual(sync_response.json()["data"]["status"], "pending")
        self.assertEqual(list_response.json()["data"][0]["name"], "Shared Drive")
        self.assertIn("last_sync_at", list_response.json()["data"][0])
        self.assertIn("total_imported_count", list_response.json()["data"][0])
        self.assertEqual(jobs_response.json()["data"][0]["connector_id"], connector_id)
        self.assertEqual(task_runs_response.json()["data"]["items"][0]["target_type"], "connector_sync_job")

    def test_connector_sync_jobs_status_filter_contract(self):
        connector = ExternalConnector(
            user_id=self.user.id,
            connector_type="drive",
            name="Ops Drive",
            status="active",
        )
        self.db.add(connector)
        self.db.commit()
        self.db.refresh(connector)

        self.db.add(ConnectorSyncJob(connector_id=connector.id, user_id=self.user.id, status="succeeded", sync_mode="manual"))
        self.db.add(ConnectorSyncJob(connector_id=connector.id, user_id=self.user.id, status="failed", sync_mode="manual"))
        self.db.commit()

        response = self.client.get(
            "/api/connectors/sync-jobs",
            headers=self.headers,
            params={"connector_id": connector.id, "status": "failed"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(len(payload["data"]), 1)
        self.assertEqual(payload["data"][0]["status"], "failed")

    def test_connector_scope_respects_department_and_org_visibility(self):
        self.user.organization_id = 501
        self.user.department_id = 601
        self.db.add(self.user)
        self.db.commit()

        same_dept_peer = User(
            username="peer_connector_scope",
            email="peer_connector_scope@example.com",
            hashed_password=hash_password("secret"),
            organization_id=501,
            department_id=601,
        )
        same_org_peer = User(
            username="org_connector_scope",
            email="org_connector_scope@example.com",
            hashed_password=hash_password("secret"),
            organization_id=501,
            department_id=999,
        )
        outsider = User(
            username="outsider_connector_scope",
            email="outsider_connector_scope@example.com",
            hashed_password=hash_password("secret"),
            organization_id=777,
            department_id=888,
        )
        self.db.add(same_dept_peer)
        self.db.add(same_org_peer)
        self.db.add(outsider)
        self.db.commit()
        self.db.refresh(same_dept_peer)
        self.db.refresh(same_org_peer)
        self.db.refresh(outsider)

        department_connector = ExternalConnector(
            user_id=self.user.id,
            organization_id=501,
            department_id=601,
            connector_type="drive",
            name="Shared Dept Drive",
            status="active",
        )
        organization_connector = ExternalConnector(
            user_id=self.user.id,
            organization_id=501,
            department_id=None,
            connector_type="wiki",
            name="Shared Org Wiki",
            status="active",
        )
        self.db.add(department_connector)
        self.db.add(organization_connector)
        self.db.commit()
        self.db.refresh(department_connector)
        self.db.refresh(organization_connector)

        self.db.add(ConnectorSyncJob(connector_id=department_connector.id, user_id=self.user.id, status="succeeded", sync_mode="manual"))
        self.db.add(ConnectorSyncJob(connector_id=organization_connector.id, user_id=self.user.id, status="succeeded", sync_mode="manual"))
        self.db.commit()

        same_dept_headers = {"Authorization": f"Bearer {create_access_token({'sub': same_dept_peer.id})}"}
        same_org_headers = {"Authorization": f"Bearer {create_access_token({'sub': same_org_peer.id})}"}
        outsider_headers = {"Authorization": f"Bearer {create_access_token({'sub': outsider.id})}"}

        same_dept_list = self.client.get("/api/connectors/", headers=same_dept_headers)
        same_dept_jobs = self.client.get("/api/connectors/sync-jobs", headers=same_dept_headers)
        same_org_list = self.client.get("/api/connectors/", headers=same_org_headers)
        same_org_jobs = self.client.get("/api/connectors/sync-jobs", headers=same_org_headers)
        outsider_list = self.client.get("/api/connectors/", headers=outsider_headers)

        self.assertEqual(same_dept_list.status_code, 200)
        same_dept_ids = {item["id"] for item in same_dept_list.json()["data"]}
        self.assertIn(department_connector.id, same_dept_ids)
        self.assertIn(organization_connector.id, same_dept_ids)
        self.assertEqual(same_dept_jobs.status_code, 200)
        same_dept_job_ids = {item["connector_id"] for item in same_dept_jobs.json()["data"]}
        self.assertIn(department_connector.id, same_dept_job_ids)
        self.assertIn(organization_connector.id, same_dept_job_ids)

        self.assertEqual(same_org_list.status_code, 200)
        same_org_ids = {item["id"] for item in same_org_list.json()["data"]}
        self.assertNotIn(department_connector.id, same_org_ids)
        self.assertIn(organization_connector.id, same_org_ids)
        self.assertEqual(same_org_jobs.status_code, 200)
        same_org_job_ids = {item["connector_id"] for item in same_org_jobs.json()["data"]}
        self.assertNotIn(department_connector.id, same_org_job_ids)
        self.assertIn(organization_connector.id, same_org_job_ids)

        self.assertEqual(outsider_list.status_code, 200)
        outsider_ids = {item["id"] for item in outsider_list.json()["data"]}
        self.assertNotIn(department_connector.id, outsider_ids)
        self.assertNotIn(organization_connector.id, outsider_ids)

    def test_connector_sync_task_imports_document_and_skips_duplicates(self):
        from app.tasks import connector_sync_task

        connector = ExternalConnector(
            user_id=self.user.id,
            connector_type="wiki",
            name="Team Wiki",
            status="active",
            config_json=json.dumps(
                {
                    "knowledge_base_name": "团队知识",
                    "seed_documents": [
                        {
                            "title": "入职指南.md",
                            "content": "# 入职指南\n\n第一天完成账号开通。",
                            "file_type": "md",
                            "classification": "hr",
                            "tags": ["hr", "onboarding"],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        )
        self.db.add(connector)
        self.db.commit()
        self.db.refresh(connector)

        class FakeTaskSelf:
            request = type("Req", (), {"id": "connector-sync-task-1", "retries": 0})()

            @staticmethod
            def update_state(*args, **kwargs):
                return None

            @staticmethod
            def retry(exc=None, countdown=None, max_retries=None):
                raise exc

        first_job = ConnectorSyncJob(connector_id=connector.id, user_id=self.user.id, status="pending", sync_mode="manual")
        self.db.add(first_job)
        self.db.commit()
        self.db.refresh(first_job)

        with patch("app.tasks.SessionLocal", side_effect=self.TestingSessionLocal), patch("app.tasks.log_async_task_event", return_value=None), patch("app.services.document_service.rag_service.index_document", return_value=None):
            first_result = connector_sync_task.run.__func__(FakeTaskSelf(), first_job.id)

        self.assertEqual(first_result["imported_count"], 1)
        self.assertEqual(first_result["skipped_count"], 0)
        docs = self.db.query(Document).filter(Document.user_id == self.user.id, Document.title == "入职指南.md").all()
        self.assertEqual(len(docs), 1)
        self.db.expire_all()
        first_job = self.db.query(ConnectorSyncJob).filter(ConnectorSyncJob.id == first_job.id).first()
        first_detail = json.loads(first_job.result_detail_json)
        self.assertIn("入职指南.md", first_detail["imported_titles"])
        self.assertEqual(first_detail["imported_items"][0]["title"], "入职指南.md")
        self.assertEqual(first_detail["imported_items"][0]["document_id"], docs[0].id)

        second_job = ConnectorSyncJob(connector_id=connector.id, user_id=self.user.id, status="pending", sync_mode="manual")
        self.db.add(second_job)
        self.db.commit()
        self.db.refresh(second_job)

        with patch("app.tasks.SessionLocal", side_effect=self.TestingSessionLocal), patch("app.tasks.log_async_task_event", return_value=None), patch("app.services.document_service.rag_service.index_document", return_value=None):
            second_result = connector_sync_task.run.__func__(FakeTaskSelf(), second_job.id)

        self.assertEqual(second_result["imported_count"], 0)
        self.assertEqual(second_result["skipped_count"], 1)
        docs = self.db.query(Document).filter(Document.user_id == self.user.id, Document.title == "入职指南.md").all()
        self.assertEqual(len(docs), 1)
        self.db.expire_all()
        second_job = self.db.query(ConnectorSyncJob).filter(ConnectorSyncJob.id == second_job.id).first()
        second_detail = json.loads(second_job.result_detail_json)
        self.assertIn("入职指南.md", second_detail["skipped_titles"])
        self.assertEqual(second_detail["skipped_items"][0]["document_id"], docs[0].id)

    def test_connector_sync_task_imports_local_directory_documents(self):
        from app.tasks import connector_sync_task

        with tempfile.TemporaryDirectory(dir=".") as tmpdir:
            source_dir = Path(tmpdir)
            (source_dir / "policies").mkdir(parents=True, exist_ok=True)
            (source_dir / "policies" / "leave.md").write_text("# 请假制度\n\n请假需要提前审批。", encoding="utf-8")
            (source_dir / "faq.txt").write_text("办公区门禁开放时间：7:30-21:00", encoding="utf-8")

            connector = ExternalConnector(
                user_id=self.user.id,
                connector_type="drive",
                name="Shared Drive",
                status="active",
                config_json=json.dumps(
                    {
                        "path": str(source_dir),
                        "knowledge_base_name": "共享盘知识",
                    },
                    ensure_ascii=False,
                ),
            )
            self.db.add(connector)
            self.db.commit()
            self.db.refresh(connector)

            class FakeTaskSelf:
                request = type("Req", (), {"id": "connector-sync-task-dir-1", "retries": 0})()

                @staticmethod
                def update_state(*args, **kwargs):
                    return None

                @staticmethod
                def retry(exc=None, countdown=None, max_retries=None):
                    raise exc

            sync_job = ConnectorSyncJob(connector_id=connector.id, user_id=self.user.id, status="pending", sync_mode="manual")
            self.db.add(sync_job)
            self.db.commit()
            self.db.refresh(sync_job)

            with patch("app.tasks.SessionLocal", side_effect=self.TestingSessionLocal), patch("app.tasks.log_async_task_event", return_value=None), patch("app.services.document_service.rag_service.index_document", return_value=None):
                result = connector_sync_task.run.__func__(FakeTaskSelf(), sync_job.id)

            self.assertEqual(result["imported_count"], 2)
            docs = self.db.query(Document).filter(Document.user_id == self.user.id).order_by(Document.title.asc()).all()
            titles = {doc.title for doc in docs}
            self.assertIn("faq.txt", titles)
            self.assertIn("policies/leave.md", titles)

    def test_connector_sync_task_imports_local_excel_document(self):
        from app.tasks import connector_sync_task

        with tempfile.TemporaryDirectory(dir=".") as tmpdir:
            source_dir = Path(tmpdir)
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "排期"
            sheet["A1"] = "负责人"
            sheet["B1"] = "截止时间"
            sheet["A2"] = "李雷"
            sheet["B2"] = "周五"
            workbook.save(source_dir / "schedule.xlsx")

            connector = ExternalConnector(
                user_id=self.user.id,
                connector_type="drive",
                name="Ops Drive",
                status="active",
                config_json=json.dumps({"path": str(source_dir), "knowledge_base_name": "运营资料"}, ensure_ascii=False),
            )
            self.db.add(connector)
            self.db.commit()
            self.db.refresh(connector)

            class FakeTaskSelf:
                request = type("Req", (), {"id": "connector-sync-task-xlsx-1", "retries": 0})()

                @staticmethod
                def update_state(*args, **kwargs):
                    return None

                @staticmethod
                def retry(exc=None, countdown=None, max_retries=None):
                    raise exc

            sync_job = ConnectorSyncJob(connector_id=connector.id, user_id=self.user.id, status="pending", sync_mode="manual")
            self.db.add(sync_job)
            self.db.commit()
            self.db.refresh(sync_job)

            with patch("app.tasks.SessionLocal", side_effect=self.TestingSessionLocal), patch("app.tasks.log_async_task_event", return_value=None), patch("app.services.document_service.rag_service.index_document", return_value=None):
                result = connector_sync_task.run.__func__(FakeTaskSelf(), sync_job.id)

            self.assertEqual(result["imported_count"], 1)
            doc = self.db.query(Document).filter(Document.user_id == self.user.id, Document.title == "schedule.xlsx").first()
            self.assertIsNotNone(doc)
            self.assertEqual(doc.file_type, "xlsx")

    def test_connector_sync_task_respects_max_files_and_extension_filters(self):
        from app.tasks import connector_sync_task

        with tempfile.TemporaryDirectory(dir=".") as tmpdir:
            source_dir = Path(tmpdir)
            (source_dir / "a.md").write_text("# A", encoding="utf-8")
            (source_dir / "b.txt").write_text("B", encoding="utf-8")
            (source_dir / "c.csv").write_text("name,value\nx,1", encoding="utf-8")
            (source_dir / "d.docx").write_bytes(b"not-a-real-docx")

            connector = ExternalConnector(
                user_id=self.user.id,
                connector_type="drive",
                name="Filtered Drive",
                status="active",
                config_json=json.dumps(
                    {
                        "path": str(source_dir),
                        "knowledge_base_name": "过滤资料",
                        "include_extensions": ["md", ".txt", "csv"],
                        "exclude_extensions": [".csv"],
                        "max_files": 2,
                        "recursive": False,
                    },
                    ensure_ascii=False,
                ),
            )
            self.db.add(connector)
            self.db.commit()
            self.db.refresh(connector)

            class FakeTaskSelf:
                request = type("Req", (), {"id": "connector-sync-task-filter-1", "retries": 0})()

                @staticmethod
                def update_state(*args, **kwargs):
                    return None

                @staticmethod
                def retry(exc=None, countdown=None, max_retries=None):
                    raise exc

            sync_job = ConnectorSyncJob(connector_id=connector.id, user_id=self.user.id, status="pending", sync_mode="manual")
            self.db.add(sync_job)
            self.db.commit()
            self.db.refresh(sync_job)

            with patch("app.tasks.SessionLocal", side_effect=self.TestingSessionLocal), patch("app.tasks.log_async_task_event", return_value=None), patch("app.services.document_service.rag_service.index_document", return_value=None):
                result = connector_sync_task.run.__func__(FakeTaskSelf(), sync_job.id)

            self.assertEqual(result["imported_count"], 2)
            titles = {
                doc.title
                for doc in self.db.query(Document).filter(Document.user_id == self.user.id).all()
                if doc.title in {"a.md", "b.txt", "c.csv", "d.docx"}
            }
            self.assertEqual(titles, {"a.md", "b.txt"})


if __name__ == "__main__":
    unittest.main()
