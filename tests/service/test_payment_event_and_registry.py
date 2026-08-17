"""Service/Task 层：task_run_registry 注册表与 payment_event_service 验签/幂等/脱敏补测。

覆盖：
- app/tasks/task_run_registry.py：注册/查询/内置条目、business_key 与 context 推导；
- app/services/billing/payment_event_service.py：verify_signature fail-closed 分支、
  敏感字段脱敏（_redact）、occurred_at 解析、record_event 幂等。
"""

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.document import Document
from app.models.org import Organization
from app.models.payment_event import PaymentEvent
from app.models.user import User
from app.services.billing.payment_event_service import (
    WebhookRejectedError,
    _redact,
    payment_event_service,
)
from app.tasks import task_run_registry as ttr


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


class TaskRunRegistryTests(unittest.TestCase):
    def test_builtin_specs_registered(self):
        self.assertIsNotNone(ttr.get_spec("parse_document"))
        self.assertIsNotNone(ttr.get_spec("run_audit_export"))
        self.assertIn("process_open_contract_review", {s.task_name for s in ttr.all_specs()})

    def test_register_and_get(self):
        spec = ttr.TaskRunSpec(task_name="test_task_x", queue="test",
                               business_key_fn=lambda *a: "key", context_fn=None)
        ttr.register(spec)
        try:
            self.assertEqual(ttr.get_spec("test_task_x"), spec)
        finally:
            ttr._TASK_SPECS.pop("test_task_x", None)

    def test_document_key_and_context(self):
        self.assertEqual(ttr._document_key(7), "document:7")
        engine = _engine()
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        db = Session()
        try:
            self.assertEqual(ttr._document_context(db, 999), {})
            org = Organization(name="R", code="R01")
            db.add(org)
            db.commit()
            user = User(username="r", email="r@example.com", hashed_password="h",
                        organization_id=org.id)
            db.add(user)
            db.commit()
            doc = Document(user_id=user.id, title="d", file_type="md", status="uploaded",
                           organization_id=org.id)
            db.add(doc)
            db.commit()
            db.refresh(doc)
            ctx = ttr._document_context(db, doc.id)
            self.assertEqual(ctx, {"tenant_id": org.id, "user_id": user.id})
        finally:
            db.close()
            engine.dispose()

    def test_user_context_at(self):
        engine = _engine()
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        db = Session()
        try:
            fn = ttr._user_context_at(1)
            self.assertEqual(fn(db), {})  # 参数不足
            self.assertEqual(fn(db, 1, 999), {})  # 用户不存在
            org = Organization(name="R2", code="R02")
            db.add(org)
            db.commit()
            user = User(username="r2", email="r2@example.com", hashed_password="h",
                        organization_id=org.id)
            db.add(user)
            db.commit()
            db.refresh(user)
            self.assertEqual(fn(db, 1, user.id), {"tenant_id": org.id, "user_id": user.id})
        finally:
            db.close()
            engine.dispose()

    def test_async_job_and_connector_keys(self):
        self.assertEqual(ttr._async_job_key(3), "legal_async_job:3")
        self.assertEqual(ttr._connector_key(9), "connector:9")


class PaymentEventServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    # ── 验签 fail-closed ────────────────────────────────────────────────────
    def test_verify_without_secret_fails_closed_when_required(self):
        with patch("app.services.billing.payment_event_service.get_settings") as settings:
            settings.return_value.PAYMENT_WEBHOOK_REQUIRE_SIGNATURE = True
            settings.return_value.PAYMENT_WEBHOOK_SECRET = ""
            with self.assertRaises(WebhookRejectedError) as ctx:
                payment_event_service.verify_signature(b"{}", "sig")
        self.assertEqual(ctx.exception.code, "WEBHOOK_SIGNATURE_NOT_CONFIGURED")

    def test_verify_without_secret_skips_when_not_required(self):
        with patch("app.services.billing.payment_event_service.get_settings") as settings:
            settings.return_value.PAYMENT_WEBHOOK_REQUIRE_SIGNATURE = False
            settings.return_value.PAYMENT_WEBHOOK_SECRET = ""
            payment_event_service.verify_signature(b"{}", None)  # 不抛（显式关闭验签）

    def test_verify_invalid_signature_maps_error_code(self):
        with patch("app.services.billing.payment_event_service.get_settings") as settings:
            settings.return_value.PAYMENT_WEBHOOK_REQUIRE_SIGNATURE = True
            settings.return_value.PAYMENT_WEBHOOK_SECRET = "s3cret"
            settings.return_value.PAYMENT_WEBHOOK_SIGNATURE_TOLERANCE_SECONDS = 300
            with self.assertRaises(WebhookRejectedError) as ctx:
                payment_event_service.verify_signature(b'{"x":1}', "t=1,v1=deadbeef")
        self.assertEqual(ctx.exception.code, "INVALID_WEBHOOK_SIGNATURE")

    # ── 脱敏 ────────────────────────────────────────────────────────────────
    def test_redact_sensitive_keys_recursively(self):
        payload = {
            "data": {"object": {"number": "4242", "cvv": "123", "name": "张三"}},
            "events": [{"client_secret": "s1"}],
            "amount": 100,
        }
        redacted = _redact(payload)
        self.assertEqual(redacted["data"]["object"]["number"], "***")
        self.assertEqual(redacted["data"]["object"]["cvv"], "***")
        self.assertEqual(redacted["events"][0]["client_secret"], "***")
        self.assertEqual(redacted["data"]["object"]["name"], "张三")  # 非敏感键保留
        self.assertEqual(redacted["amount"], 100)

    # ── occurred_at 解析 ────────────────────────────────────────────────────
    def test_parse_occurred_variants(self):
        self.assertIsNotNone(payment_event_service._parse_occurred({"created": 1720000000}, None))
        self.assertIsNotNone(payment_event_service._parse_occurred({"occurred_at": "2026-08-01T10:00:00Z"}, None))
        self.assertIsNone(payment_event_service._parse_occurred({}, None))
        self.assertIsNone(payment_event_service._parse_occurred({"created": "not-a-date"}, None))

    # ── record_event 幂等 ───────────────────────────────────────────────────
    def test_record_event_idempotent(self):
        payload = {"id": "evt_1", "data": {"object": {"id": "sub_1", "object": "subscription"}}}
        first = payment_event_service.record_event(
            db=self.db, provider="stripe", provider_event_id="evt_1",
            event_type="customer.subscription.updated", raw_payload=payload,
        )
        second = payment_event_service.record_event(
            db=self.db, provider="stripe", provider_event_id="evt_1",
            event_type="customer.subscription.updated", raw_payload=payload,
        )
        self.assertEqual(first.id, second.id)  # 重复事件返回既有
        self.assertEqual(self.db.query(PaymentEvent).count(), 1)
        self.assertEqual(first.status, "pending")
        self.assertEqual(first.object_type, "subscription")
        self.assertEqual(first.object_id, "sub_1")

    def test_record_event_different_provider_isolated(self):
        payload = {"id": "evt_2", "data": {}}
        payment_event_service.record_event(
            db=self.db, provider="stripe", provider_event_id="evt_2", event_type="x", raw_payload=payload)
        payment_event_service.record_event(
            db=self.db, provider="wechat", provider_event_id="evt_2", event_type="x", raw_payload=payload)
        self.assertEqual(self.db.query(PaymentEvent).count(), 2)


if __name__ == "__main__":
    unittest.main()
