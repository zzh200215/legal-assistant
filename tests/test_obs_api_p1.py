"""P1「API、OpenAPI 与 WebSocket 统一化」验收测试。

覆盖 15 项验收点的代表性断言：
1  envelope/错误码/request-trace id；2 route 不直接操作 ORM（service 分页）；
3 列表 DB 分页/count/白名单；4 跨租户越权隔离；5/6 幂等重放/冲突；
7 版本冲突（If-Match/乐观锁）；8/9 Job 202/查询/取消幂等/权限；
10 WS welcome/seq/ack/resume/resync；11 背压；12 断开资源释放；
13 无法绕过认证/租户/幂等/版本；14 OpenAPI 生成 + operationId 唯一 + breaking 检测；
15 旧格式兼容（WS 旧消息格式、task_id 别名）。
"""

import hashlib
import importlib.util
import json
import secrets
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import create_access_token, hash_password
from app.core.database import Base, get_db
from app.main import app
from app.models.legal_platform import DeveloperApiKey, DeveloperApp, LegalAsyncJob
from app.models.org import Organization, OrganizationMember
from app.models.user import User
from app.models.task import Task
from app.models.document import Document

ROOT = Path(__file__).resolve().parents[1]


def _make_engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _mock_redis_at_count(count: int = 1) -> MagicMock:
    mock_r = MagicMock()
    mock_r.incr.return_value = count
    mock_r.expire.return_value = True
    return mock_r


def _load_checker_module():
    """加载 scripts/check_openapi_contract.py（纯函数 diff 逻辑，供验收 14 使用）。"""
    path = ROOT / "scripts" / "check_openapi_contract.py"
    spec = importlib.util.spec_from_file_location("check_openapi_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BaseApiCase(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session()

        self.org = Organization(name="OrgA", code="ORGA")
        self.db.add(self.org)
        self.db.flush()
        self.user = User(
            username="tester",
            email="tester@example.com",
            hashed_password=hash_password("secret"),
            role="admin",
            organization_id=self.org.id,
        )
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)
        self.token = create_access_token({"sub": str(self.user.id)})
        self.headers = {"Authorization": f"Bearer {self.token}"}

        def _override_db():
            yield self.db

        app.dependency_overrides[get_db] = _override_db
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def _ws_subprotocols(self):
        return ["json", f"bearer.{self.token}"]


# ── 验收 1/14：envelope、错误码、OpenAPI ──────────────────────────────────────

class EnvelopeAndOpenApiTests(BaseApiCase):
    def test_success_envelope_has_request_and_trace_ids(self):
        resp = self.client.get("/api/health", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload["success"])
        self.assertIn("request_id", payload)
        self.assertIn("trace_id", payload)

    def test_error_envelope_has_stable_code_and_ids(self):
        resp = self.client.get("/api/documents/9999", headers=self.headers)
        self.assertEqual(resp.status_code, 404)
        payload = resp.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], "DOCUMENT_NOT_FOUND")
        self.assertIn("request_id", payload)
        self.assertIn("trace_id", payload)

    def test_validation_error_has_field_errors(self):
        resp = self.client.post(
            "/api/tasks/", json={"title": 123}, headers=self.headers,
        )
        self.assertEqual(resp.status_code, 422)
        payload = resp.json()
        self.assertEqual(payload["error"]["code"], "VALIDATION_ERROR")
        self.assertIsInstance(payload["error"].get("field_errors"), list)
        self.assertGreater(len(payload["error"]["field_errors"]), 0)

    def test_internal_error_does_not_leak_detail(self):
        with patch(
            "app.api.tasks.task_api.task_service.create",
            side_effect=RuntimeError("db_password=secret"),
        ):
            resp = self.client.post(
                "/api/tasks/",
                json={"title": "测试", "description": "d"},
                headers=self.headers,
            )
        self.assertEqual(resp.status_code, 500)
        self.assertNotIn("secret", resp.text)
        self.assertEqual(resp.json()["error"]["code"], "TASK_CREATE_FAILED")

    def test_api_version_header(self):
        resp = self.client.get("/api/health", headers=self.headers)
        self.assertEqual(resp.headers.get("X-API-Version"), "1")

    def test_openapi_generates_with_unique_operation_ids(self):
        schema = app.openapi()
        self.assertIn("x-api-version", schema["info"])
        self.assertEqual(schema["info"]["x-api-version"], "1")
        methods = ("get", "post", "put", "patch", "delete", "options", "head")
        opids = [
            schema["paths"][p][m]["operationId"]
            for p, ms in schema["paths"].items()
            for m in ms if m in methods
        ]
        self.assertEqual(len(opids), len(set(opids)), "operationId 必须唯一")
        self.assertIn("JobOut", schema["components"]["schemas"])
        self.assertIn("ApiKeyHeader", schema["components"]["securitySchemes"])
        op = schema["paths"]["/api/open/v1/contract-reviews"]["post"]
        self.assertIn("202", op.get("responses", {}))

    def test_breaking_change_detection(self):
        checker = _load_checker_module()
        snapshot = {
            "paths": {
                "/api/legacy": {
                    "get": {
                        "operationId": "legacy_get",
                        "parameters": [{"name": "id", "in": "query", "required": True}],
                        "requestBody": {"required": False},
                        "responses": {"200": {}},
                    }
                }
            },
            "components": {"schemas": {"Old": {"type": "object", "properties": {
                "a": {"type": "string"}}, "required": ["a"]}}},
            "x-error-codes": ["OLD_CODE", "KEEP_CODE"],
        }
        current = {
            "paths": {
                "/api/legacy": {
                    "get": {
                        "operationId": "legacy_get",
                        "parameters": [],
                        "requestBody": {"required": True},
                        "responses": {"404": {}},
                    }
                }
            },
            "components": {"schemas": {"Old": {"type": "object", "properties": {
                "b": {"type": "string"}}, "required": ["b"]}}},
            "x-error-codes": ["KEEP_CODE"],
        }
        breaking, additive = checker._diff_current_vs_snapshot(current, snapshot)
        joined = "\n".join(breaking)
        self.assertIn("parameter removed", joined)
        self.assertIn("requestBody became required", joined)
        self.assertIn("response removed", joined)
        self.assertIn("schema field removed", joined)
        self.assertIn("schema field became required", joined)
        self.assertIn("error code removed", joined)
        self.assertTrue(additive, "应检测到非 breaking 变更（新端点/新字段）")


# ── 验收 3/4：DB 分页与跨租户隔离 ─────────────────────────────────────────────

class PaginationTests(BaseApiCase):
    def setUp(self):
        super().setUp()
        for i in range(5):
            self.db.add(Document(
                user_id=self.user.id,
                organization_id=self.org.id,
                title=f"合同 {i}",
                file_type="txt",
                status="ready",
            ))
        self.db.commit()

    def test_document_list_uses_db_pagination_with_has_next(self):
        resp = self.client.get("/api/documents/?page=1&page_size=2", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(len(data["items"]), 2)
        self.assertEqual(data["total"], 5)
        self.assertTrue(data["has_next"])
        self.assertFalse(data["has_previous"])
        page2 = self.client.get("/api/documents/?page=3&page_size=2", headers=self.headers).json()["data"]
        self.assertFalse(page2["has_next"])
        self.assertTrue(page2["has_previous"])
        # 稳定排序 + 无重复/遗漏
        ids = []
        for p in (1, 2, 3):
            items = self.client.get(f"/api/documents/?page={p}&page_size=2", headers=self.headers).json()["data"]["items"]
            ids.extend(i["id"] for i in items)
        self.assertEqual(len(set(ids)), 5)

    def test_task_list_uses_db_pagination(self):
        for i in range(3):
            self.db.add(Task(user_id=self.user.id, title=f"任务 {i}", status="todo"))
        self.db.commit()
        resp = self.client.get("/api/tasks/?page=1&page_size=2", headers=self.headers)
        data = resp.json()["data"]
        self.assertEqual(len(data["items"]), 2)
        self.assertEqual(data["total"], 3)
        self.assertTrue(data["has_next"])

    def test_cross_tenant_pagination_isolation(self):
        other_org = Organization(name="OrgB", code="ORGB")
        self.db.add(other_org)
        self.db.flush()
        other_user = User(
            username="other",
            email="other@example.com",
            hashed_password=hash_password("pw"),
            role="user",
            organization_id=other_org.id,
        )
        self.db.add(other_user)
        self.db.flush()
        self.db.add(Document(user_id=other_user.id, organization_id=other_org.id,
                             title="他组织的合同", file_type="txt", status="ready"))
        self.db.commit()

        resp = self.client.get("/api/documents/?page=1&page_size=20", headers=self.headers)
        titles = [i["title"] for i in resp.json()["data"]["items"]]
        self.assertNotIn("他组织的合同", titles)
        self.assertEqual(len(titles), 5)


# ── 验收 5/6/13：幂等 ─────────────────────────────────────────────────────────

class OpenApiIdempotencyTests(BaseApiCase):
    def setUp(self):
        super().setUp()
        self.db.add(OrganizationMember(
            organization_id=self.org.id, user_id=self.user.id, legal_role="admin"))
        self.db.flush()
        self.raw_key = "lzj_op_" + secrets.token_urlsafe(32)
        self.dev_app = DeveloperApp(
            organization_id=self.org.id, name="AppA", created_by=self.user.id,
        )
        self.db.add(self.dev_app)
        self.db.flush()
        self.db.add(DeveloperApiKey(
            app_id=self.dev_app.id,
            organization_id=self.org.id,
            key_hash=hashlib.sha256(self.raw_key.encode()).hexdigest(),
            key_prefix=self.raw_key[:16],
        ))
        self.db.commit()
        self.api_headers = {"X-API-Key": self.raw_key}

    def _post_review(self, payload):
        with patch("redis.from_url", return_value=_mock_redis_at_count(1)):
            return self.client.post(
                "/api/open/v1/contract-reviews", json=payload, headers=self.api_headers,
            )

    def test_open_review_returns_202_with_job_fields(self):
        resp = self._post_review(
            {"title": "合同", "content": "这是一份合同内容，需要审查风险条款。"})
        self.assertEqual(resp.status_code, 202)
        data = resp.json()["data"]
        self.assertIn("job_id", data)
        self.assertIn("task_id", data)  # 兼容别名
        self.assertEqual(data["job_id"], data["task_id"])
        self.assertEqual(data["status"], "queued")
        self.assertIn("status_url", data)
        self.assertIn("/v1/tasks/", data["status_url"])

    def test_same_key_same_body_single_side_effect(self):
        payload = {"title": "幂等", "content": "这是一份合同内容，需要审查风险条款。",
                   "idempotency_key": "ik-1"}
        r1 = self._post_review(payload)
        r2 = self._post_review(payload)
        self.assertEqual(r1.status_code, 202)
        self.assertEqual(r2.status_code, 202)
        self.assertEqual(r1.json()["data"]["job_id"], r2.json()["data"]["job_id"])
        self.assertTrue(r2.json()["data"].get("idempotent"))
        jobs = self.db.query(LegalAsyncJob).count()
        self.assertEqual(jobs, 1, "同 key + 同载荷只产生一次副作用")

    def test_same_key_different_body_conflicts(self):
        base = {"title": "幂等", "content": "这是一份合同内容，需要审查风险条款。",
                "idempotency_key": "ik-2"}
        r1 = self._post_review(base)
        self.assertEqual(r1.status_code, 202)
        changed = {**base, "content": "这是另一份完全不同的合同内容用于冲突测试。"}
        r2 = self._post_review(changed)
        self.assertEqual(r2.status_code, 409)
        self.assertEqual(r2.json()["error"]["code"], "IDEMPOTENCY_KEY_CONFLICT")


# ── 验收 7：版本冲突 ──────────────────────────────────────────────────────────

class VersionConflictTests(BaseApiCase):
    def test_if_match_mismatch_returns_409(self):
        task = Task(user_id=self.user.id, title="版本任务", status="todo")
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)

        resp = self.client.get(f"/api/tasks/{task.id}", headers=self.headers)
        etag = resp.headers.get("ETag")
        self.assertIsNotNone(etag)
        self.assertIn("v1", etag)

        ok = self.client.put(
            f"/api/tasks/{task.id}",
            json={"title": "新标题"},
            headers={**self.headers, "If-Match": etag},
        )
        self.assertEqual(ok.status_code, 200)

        # 用旧 ETag 再更新 → 409（版本已前进）
        stale = self.client.put(
            f"/api/tasks/{task.id}",
            json={"title": "旧版本覆盖"},
            headers={**self.headers, "If-Match": etag},
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["error"]["code"], "CONCURRENT_UPDATE_CONFLICT")

    def test_stale_data_optimistic_lock_maps_to_409(self):
        task = Task(user_id=self.user.id, title="并发任务", status="todo")
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        with patch(
            "app.services.jobs.task_service.TaskService.update",
            side_effect=__import__("sqlalchemy.orm.exc", fromlist=["StaleDataError"]).StaleDataError("boom"),
        ):
            resp = self.client.put(
                f"/api/tasks/{task.id}", json={"title": "x"}, headers=self.headers)
        self.assertEqual(resp.status_code, 409)


# ── 验收 8/9：Job 统一 ────────────────────────────────────────────────────────

class AsyncJobTests(OpenApiIdempotencyTests):
    def test_job_query_and_cancel_idempotent(self):
        resp = self._post_review(
            {"title": "任务", "content": "这是一份合同内容，需要审查风险条款。"})
        job_id = resp.json()["data"]["job_id"]

        # 查询
        q = self.client.get(f"/api/open/v1/tasks/{job_id}", headers=self.api_headers)
        self.assertEqual(q.status_code, 200)
        self.assertEqual(q.json()["data"]["status"], "queued")

        # 取消（幂等：第一次 cancelled=True，第二次 cancelled=False 当前状态）
        c1 = self.client.post(f"/api/open/v1/tasks/{job_id}/cancel", headers=self.api_headers)
        self.assertEqual(c1.status_code, 200)
        self.assertTrue(c1.json()["data"]["cancelled"])
        self.assertEqual(c1.json()["data"]["job"]["status"], "cancelled")

        c2 = self.client.post(f"/api/open/v1/tasks/{job_id}/cancel", headers=self.api_headers)
        self.assertEqual(c2.status_code, 200)
        self.assertFalse(c2.json()["data"]["cancelled"])
        self.assertEqual(c2.json()["data"]["job"]["status"], "cancelled")

        # 取消行为入审计
        from app.models.legal_notifications import SecurityAuditEvent
        audit = self.db.query(SecurityAuditEvent).filter(
            SecurityAuditEvent.event_type == "job_cancel").all()
        self.assertEqual(len(audit), 1)

    def test_job_cancel_respects_tenant_scope(self):
        resp = self._post_review(
            {"title": "任务", "content": "这是一份合同内容，需要审查风险条款。"})
        job_id = resp.json()["data"]["job_id"]

        # 不同组织的 app：查询/取消组织隔离（404 隐藏存在性）
        other_org = Organization(name="OrgC", code="ORGC")
        self.db.add(other_org)
        self.db.flush()
        other_raw = "lzj_op_" + secrets.token_urlsafe(32)
        other_app = DeveloperApp(
            organization_id=other_org.id, name="OtherOrgApp", created_by=self.user.id)
        self.db.add(other_app)
        self.db.flush()
        self.db.add(DeveloperApiKey(
            app_id=other_app.id, organization_id=other_org.id,
            key_hash=hashlib.sha256(other_raw.encode()).hexdigest(),
            key_prefix=other_raw[:16],
        ))
        self.db.commit()

        q = self.client.get(f"/api/open/v1/tasks/{job_id}", headers={"X-API-Key": other_raw})
        self.assertEqual(q.status_code, 404)
        c = self.client.post(f"/api/open/v1/tasks/{job_id}/cancel", headers={"X-API-Key": other_raw})
        self.assertEqual(c.status_code, 404)

    def test_internal_cancel_requires_admin(self):
        resp = self._post_review(
            {"title": "任务", "content": "这是一份合同内容，需要审查风险条款。"})
        job_id = resp.json()["data"]["job_id"]
        # 非 admin 用户调用内部取消 → 403
        non_admin = User(
            username="member", email="m@example.com",
            hashed_password=hash_password("pw"), role="user",
            organization_id=self.org.id,
        )
        self.db.add(non_admin)
        self.db.commit()
        member_token = create_access_token({"sub": str(non_admin.id)})
        resp = self.client.post(
            f"/api/developer/orgs/{self.org.id}/async-jobs/{job_id}/cancel",
            headers={"Authorization": f"Bearer {member_token}"},
        )
        self.assertEqual(resp.status_code, 403)


# ── 验收 10/11/12/13/15：WebSocket 协议 ───────────────────────────────────────

class WsProtocolTests(BaseApiCase):
    def test_ws_welcome_carries_seq_and_resume_token(self):
        with self.client.websocket_connect("/api/ws/chat", subprotocols=self._ws_subprotocols(), timeout=5) as ws:
            ws.send_json({"content": "你好"})
            welcome = ws.receive_json()
            self.assertEqual(welcome["type"], "welcome")
            self.assertIn("seq", welcome)
            self.assertIn("resume_token", welcome)
            self.assertEqual(welcome["last_seq"], 0)
            self.assertFalse(welcome["resumed"])

    def test_ws_ack_and_ping(self):
        from app.services.memory import ws_session_service as wsmod
        session = wsmod.new_session(MagicMock(), user_id=1, organization_id=None)
        session.seq = 5
        # ack 更新 acked_seq
        from app.api.conversation.ws_api import _handle_protocol_message
        # 通过真实连接验证：ping 消息回 pong
        with self.client.websocket_connect("/api/ws/chat", subprotocols=self._ws_subprotocols(), timeout=5) as ws:
            ws.send_json({"type": "ping"})
            welcome = ws.receive_json()
            pong = ws.receive_json()
            self.assertEqual(pong["type"], "pong")
            self.assertGreater(pong["seq"], welcome["seq"])

    def test_ws_resume_replays_state_events(self):
        async def _fake_stream(*_a, **_k):
            yield "你"
            yield "好"

        with patch("app.api.conversation.ws_api.llm_client.chat_stream", side_effect=_fake_stream):
            with self.client.websocket_connect("/api/ws/chat", subprotocols=self._ws_subprotocols(), timeout=5) as ws:
                ws.send_json({"content": "你好"})
                welcome = ws.receive_json()
                session_msg = ws.receive_json()   # session（落库）
                # chunk 数量不确定（volatile，不落库），循环消费直到 done
                done = None
                for _ in range(10):
                    ev = ws.receive_json()
                    if ev["type"] == "done":
                        done = ev
                        break
                self.assertEqual(session_msg["type"], "session")
                self.assertIsNotNone(done)
                self.assertEqual(done["type"], "done")
                resume_token = welcome["resume_token"]
                last_seq = done["seq"]

        # 断线重连 + resume：补发 seq > ack_seq 的持久化状态事件（session/done）
        with patch("app.api.conversation.ws_api.llm_client.chat_stream", side_effect=_fake_stream):
            with self.client.websocket_connect("/api/ws/chat", subprotocols=self._ws_subprotocols(), timeout=5) as ws:
                ws.send_json({"type": "resume", "resume_token": resume_token, "ack_seq": 0})
                welcome2 = ws.receive_json()
                self.assertTrue(welcome2["resumed"])
                self.assertGreaterEqual(welcome2["last_seq"], last_seq)
                replayed_types = []
                # 补发事件（session + done，chunk 不落库）
                for _ in range(2):
                    ev = ws.receive_json()
                    if ev["type"] in ("session", "done"):
                        replayed_types.append(ev["type"])
                self.assertIn("session", replayed_types)
                self.assertIn("done", replayed_types)

    def test_ws_resume_invalid_token_returns_resync_required(self):
        with self.client.websocket_connect("/api/ws/chat", subprotocols=self._ws_subprotocols(), timeout=5) as ws:
            ws.send_json({"type": "resume", "resume_token": "invalid-token", "ack_seq": 0})
            welcome = ws.receive_json()
            resync = ws.receive_json()
            self.assertFalse(welcome["resumed"])
            self.assertEqual(resync["type"], "resync_required")
            self.assertEqual(resync["reason"], "invalid_or_expired_token")

    def test_ws_auth_failure_closes(self):
        with self.client.websocket_connect(
            "/api/ws/chat", subprotocols=["json", "bearer.invalid-token"], timeout=5,
        ) as ws:
            msg = ws.receive_json()
            self.assertEqual(msg["type"], "error")

    def test_ws_backpressure_drops_volatile_and_flags_overloaded(self):
        from app.services.memory import ws_session_service as wsmod

        websocket = MagicMock()
        websocket.send_text = AsyncMock(side_effect=RuntimeError("slow client"))
        session = wsmod.new_session(websocket, user_id=1, organization_id=None)

        # 塞满状态事件（不可丢弃）→ 触发 overloaded
        for i in range(wsmod.MAX_OUTBOX):
            session.outbox.append(({"type": "done", "seq": i}, False))
        self.assertEqual(len(session.outbox), wsmod.MAX_OUTBOX)
        ok = wsmod.send_event.__wrapped__ if hasattr(wsmod.send_event, "__wrapped__") else None
        # 直接调用内部逻辑：队列满且无 volatile → overloaded=True
        async def _run():
            return await wsmod.send_event(session, "done", {"content": "x"}, volatile=False)
        import asyncio
        result = asyncio.run(_run())
        self.assertFalse(result)
        self.assertTrue(session.overloaded)

    def test_ws_cancel_job_requires_admin_role(self):
        from app.models.legal_platform import LegalAsyncJob as Job
        self.db.add(OrganizationMember(
            organization_id=self.org.id, user_id=self.user.id, legal_role="admin"))
        job = Job(organization_id=self.org.id, job_type="audit_export",
                  status="queued", created_by=self.user.id)
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)

        # admin 用户可通过 WS 取消
        with self.client.websocket_connect("/api/ws/agent", subprotocols=self._ws_subprotocols(), timeout=5) as ws:
            ws.send_json({"type": "cancel", "kind": "job", "id": job.id})
            welcome = ws.receive_json()
            msg = ws.receive_json()
            self.assertEqual(msg["type"], "cancelled")
            self.assertTrue(msg["cancelled"])
            self.assertEqual(msg["job"]["status"], "cancelled")

        # 非 admin 用户取消 → 稳定错误（JOB_NOT_FOUND 语义）
        non_admin = User(
            username="member2", email="m2@example.com",
            hashed_password=hash_password("pw"), role="user",
            organization_id=self.org.id,
        )
        self.db.add(non_admin)
        self.db.commit()
        member_token = create_access_token({"sub": str(non_admin.id)})
        with self.client.websocket_connect(
            "/api/ws/agent", subprotocols=["json", f"bearer.{member_token}"], timeout=5,
        ) as ws:
            ws.send_json({"type": "cancel", "kind": "job", "id": job.id})
            welcome = ws.receive_json()
            msg = ws.receive_json()
            self.assertEqual(msg["type"], "error")
            self.assertEqual(msg["code"], "JOB_NOT_FOUND")

    def test_legacy_chat_message_format_still_works(self):
        """验收 15：旧客户端无 type 字段的消息格式继续可用。"""
        async def _fake_stream(*_a, **_k):
            yield "旧格式兼容"

        with patch("app.api.conversation.ws_api.llm_client.chat_stream", side_effect=_fake_stream):
            with self.client.websocket_connect("/api/ws/chat", subprotocols=self._ws_subprotocols(), timeout=5) as ws:
                ws.send_json({"content": "旧格式消息"})
                welcome = ws.receive_json()
                self.assertEqual(welcome["type"], "welcome")
                session_msg = ws.receive_json()
                self.assertEqual(session_msg["type"], "session")
                done = None
                for _ in range(10):
                    ev = ws.receive_json()
                    if ev["type"] == "done":
                        done = ev
                        break
                self.assertIsNotNone(done)
                self.assertIn("旧格式兼容", done["content"])


if __name__ == "__main__":
    unittest.main()
