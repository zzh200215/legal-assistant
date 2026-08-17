"""# 等保差距 #2：结构化日志导出 + 集中检索测试"""
import json
import logging
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import create_access_token, hash_password
from app.core.database import Base, get_db
from app.core.observability import structured_log_json
from app.main import app
from app.models.auth_log import AdminAuditLog, LoginLog
from app.models.operation_log import OperationLog
from app.models.user import User, UserStatus
from app.services.observability.audit_log_service import audit_log_service
from app.services.observability.oplog_service import oplog_service

AUDIT_LOGGER = "audit.json"


def _make_engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


class _CaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record.getMessage())


def _capture_audit_logger():
    logger = logging.getLogger(AUDIT_LOGGER)
    handler = _CaptureHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger, handler


class StructuredLogTests(unittest.TestCase):
    def _settings(self, enabled):
        mock_settings = MagicMock()
        mock_settings.STRUCTURED_LOG_JSON_LINES = enabled
        return mock_settings

    def test_disabled_by_default_emits_nothing(self):
        logger, handler = _capture_audit_logger()
        try:
            with patch("app.core.observability.get_settings", return_value=self._settings(False)):
                structured_log_json(source="operation_log", action="create")
        finally:
            logger.removeHandler(handler)
        self.assertEqual(handler.records, [])

    def test_enabled_emits_json_line(self):
        logger, handler = _capture_audit_logger()
        try:
            with patch("app.core.observability.get_settings", return_value=self._settings(True)):
                structured_log_json(
                    source="audit_log", module="audit", action="user_disable",
                    actor="admin", target_type="user", target_id=3, detail="禁用账号", ip_address="1.2.3.4",
                )
        finally:
            logger.removeHandler(handler)
        self.assertEqual(len(handler.records), 1)
        payload = json.loads(handler.records[0])
        self.assertEqual(payload["source"], "audit_log")
        self.assertEqual(payload["action"], "user_disable")
        self.assertEqual(payload["actor"], "admin")
        self.assertEqual(payload["ip_address"], "1.2.3.4")

    def test_auto_file_handler_writes_audit_file_and_is_idempotent(self):
        import os
        import tempfile
        from pathlib import Path

        import app.core.observability as observability

        with tempfile.TemporaryDirectory() as tmp:
            audit_file = Path(tmp) / "nested" / "audit.jsonl"  # 验证目录自动创建
            mock_settings = MagicMock()
            mock_settings.STRUCTURED_LOG_JSON_LINES = True
            mock_settings.STRUCTURED_LOG_FILE = str(audit_file)

            # 重置模块级标志与 logger handlers，确保本次独立
            observability._audit_handler_configured = False
            logger = logging.getLogger(AUDIT_LOGGER)
            saved_handlers = list(logger.handlers)
            logger.handlers.clear()
            logger.propagate = False
            try:
                with patch("app.core.observability.get_settings", return_value=mock_settings):
                    structured_log_json(source="operation_log", action="upgrade_intent", actor="demo")
                    structured_log_json(source="login_log", action="login_success", actor="demo")
            finally:
                observability._audit_handler_configured = False
                logger.handlers = saved_handlers

            self.assertTrue(audit_file.exists(), "STRUCTURED_LOG_FILE 目录应自动创建并落盘")
            lines = audit_file.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2, "两行日志各占一行 JSON")
            for line in lines:
                obj = json.loads(line)
                self.assertIn(obj["source"], {"operation_log", "login_log"})

    def test_oplog_and_audit_writes_emit_structured_line(self):
        engine = _make_engine()
        Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        db = Session()
        user = User(username="op1", email="op1@t.com", hashed_password=hash_password("pw"),
                    role="admin", status=UserStatus.active.value)
        db.add(user)
        db.commit()
        logger, handler = _capture_audit_logger()
        try:
            with patch("app.core.observability.get_settings") as mock_get:
                mock_get.return_value = MagicMock()
                mock_get.return_value.STRUCTURED_LOG_JSON_LINES = True
                oplog_service.log(db=db, module="legal_consultation", action="create", user_id=user.id, detail="咨询")
                audit_log_service.log(db=db, operator=user, action="user_disable", detail="禁用")
        finally:
            logger.removeHandler(handler)
        sources = {json.loads(line)["source"] for line in handler.records}
        self.assertEqual(sources, {"operation_log", "audit_log"})
        db.close()
        engine.dispose()


class SearchLogsEndpointTests(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session()

        self.admin = User(username="admin1", email="admin1@t.com", hashed_password=hash_password("pw"),
                          role="admin", status=UserStatus.active.value)
        self.user = User(username="user1", email="user1@t.com", hashed_password=hash_password("pw"),
                         role="user", status=UserStatus.active.value)
        self.db.add_all([self.admin, self.user])
        self.db.commit()

        self.db.add_all([
            OperationLog(user_id=self.user.id, module="legal_consultation", action="create", detail="发起咨询"),
            OperationLog(user_id=self.user.id, module="legal_review", action="create", detail="发起审查"),
            AdminAuditLog(operator_id=self.admin.id, operator_name="admin1", action="user_disable",
                          target_type="user", target_id=self.user.id, detail="禁用账号"),
            LoginLog(user_id=self.user.id, username="user1", event_type="login_failed", detail="密码错误"),
        ])
        self.db.commit()

        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)
        self.admin_token = create_access_token({"sub": str(self.admin.id)})
        self.user_token = create_access_token({"sub": str(self.user.id)})

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()
        self.engine.dispose()

    def _auth(self, token):
        return {"Authorization": f"Bearer {token}"}

    def test_non_admin_forbidden(self):
        resp = self.client.get("/api/admin/logs/search", headers=self._auth(self.user_token))
        self.assertEqual(resp.status_code, 403)

    def test_search_merges_all_tracks(self):
        resp = self.client.get("/api/admin/logs/search", headers=self._auth(self.admin_token))
        self.assertEqual(resp.status_code, 200)
        items = resp.json()["data"]["items"]
        sources = {(item["source"], item["action"]) for item in items}
        self.assertEqual(sources, {
            ("operation_log", "create"),
            ("audit_log", "user_disable"),
            ("login_log", "login_failed"),
        })

    def test_keyword_filters_across_fields(self):
        resp = self.client.get(
            "/api/admin/logs/search", params={"keyword": "咨询"}, headers=self._auth(self.admin_token),
        )
        items = resp.json()["data"]["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source"], "operation_log")
        self.assertIn("咨询", items[0]["detail"])

    def test_action_and_source_filter(self):
        resp = self.client.get(
            "/api/admin/logs/search", params={"source": "login_log", "action": "login_failed"},
            headers=self._auth(self.admin_token),
        )
        items = resp.json()["data"]["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source"], "login_log")

    def test_pagination(self):
        resp = self.client.get(
            "/api/admin/logs/search", params={"page": 1, "page_size": 2}, headers=self._auth(self.admin_token),
        )
        data = resp.json()["data"]
        self.assertEqual(data["total"], 4)
        self.assertEqual(len(data["items"]), 2)


if __name__ == "__main__":
    unittest.main()
