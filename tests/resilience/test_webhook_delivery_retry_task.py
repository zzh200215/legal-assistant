"""韧性测试：Webhook 投递重试任务的指数退避 / 上限 / 签名 / 幂等契约。

覆盖 app/tasks/notification_tasks.py::retry_failed_webhook_deliveries_task：
- 指数退避（30 * 4^attempt_count）未到时间跳过；
- attempt_count 上限（<3）停止重试；
- HMAC-SHA256 签名头（X-Signature=sha256=...）与事件头契约；
- 签名密钥未轮换 → 置 failed（安全红线，A hash cannot sign a payload）；
- 应用不活跃/无 URL → 跳过；
- 失败响应仅记脱敏摘要，状态保留可重试；
- 成功 → status=success，幂等事件 ID 唯一。
"""

import unittest
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.core.time import utc_now
from app.models.legal_platform import DeveloperApp, WebhookDelivery
from app.models.org import Organization
from app.models.user import User
from app.tasks.notification_tasks import retry_failed_webhook_deliveries_task


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


class WebhookDeliveryRetryTaskTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()
        self.org = Organization(name="WebhookOrg", code="WHK")
        self.db.add(self.org)
        self.db.commit()
        self.user = User(username="wh", email="wh@example.com", hashed_password="h",
                         role="admin", organization_id=self.org.id)
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

        self._patchers = [
            patch("app.tasks.notification_tasks.SessionLocal", self.Session),
            patch("app.tasks.notification_tasks._record_beat_heartbeat"),
            # beat 锁确定性阻断 redis（fail-open 放行）
            patch(
                "app.tasks.runtime.redis.from_url",
                side_effect=RuntimeError("redis unavailable in unit tests"),
            ),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in reversed(self._patchers):
            p.stop()
        self.db.close()
        self.engine.dispose()

    def _app(self, *, status="active", secret="rotated-secret", url="https://hooks.example.com/cb") -> DeveloperApp:
        app = DeveloperApp(
            organization_id=self.org.id, name="webhook-app", status=status,
            webhook_url=url, webhook_secret_ciphertext=secret,
            created_by=self.user.id,
        )
        self.db.add(app)
        self.db.commit()
        self.db.refresh(app)
        return app

    def _delivery(self, app: DeveloperApp, *, status="pending", attempts=0, last_at=None,
                  event_id="evt-1") -> WebhookDelivery:
        delivery = WebhookDelivery(
            app_id=app.id, organization_id=self.org.id,
            event_type="legal.case.updated", event_id=event_id,
            status=status, attempt_count=attempts, last_attempted_at=last_at,
        )
        self.db.add(delivery)
        self.db.commit()
        self.db.refresh(delivery)
        return delivery

    def _run_task(self):
        return retry_failed_webhook_deliveries_task.run()

    def test_delivers_with_hmac_signature_headers(self):
        app = self._app()
        delivery = self._delivery(app)
        captured = {}

        def _fake_call(fn, **kwargs):
            captured["url"] = kwargs.get("url")
            resp = fn()
            return resp

        with patch("httpx.post") as post, patch(
            "app.core.external_resilience.external_resilience.call", side_effect=_fake_call
        ) as call:
            post.return_value = type("R", (), {"status_code": 200, "text": "", "raise_for_status": lambda self: None})()
            result = self._run_task()
        self.assertEqual(result, {"retried": 1})
        self.db.refresh(delivery)
        self.assertEqual(delivery.status, "success")
        self.assertEqual(delivery.response_status, 200)
        call.assert_called_once()
        # 请求头契约：事件头 + HMAC 签名
        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["X-Event-Type"], "legal.case.updated")
        self.assertEqual(headers["X-Event-Id"], "evt-1")
        self.assertTrue(headers["X-Signature"].startswith("sha256="))
        self.assertEqual(len(headers["X-Signature"].split("=")[1]), 64)
        self.assertEqual(post.call_args.kwargs["timeout"], 5.0)
        self.assertEqual(captured["url"], "https://hooks.example.com/cb")

    def test_exponential_backoff_skips_until_due(self):
        app = self._app()
        delivery = self._delivery(app, attempts=1, last_at=utc_now())
        with patch("httpx.post") as post:
            result = self._run_task()  # backoff=120s 未到 → 跳过
        self.assertEqual(result, {"retried": 0})
        post.assert_not_called()
        self.db.refresh(delivery)
        self.assertEqual(delivery.status, "pending")

    def test_backoff_expired_delivers(self):
        app = self._app()
        delivery = self._delivery(app, attempts=1, last_at=utc_now() - timedelta(minutes=10))
        with patch("httpx.post") as post, patch(
            "app.core.external_resilience.external_resilience.call", side_effect=lambda fn, **kw: fn()
        ):
            post.return_value = type("R", (), {"status_code": 200, "text": "", "raise_for_status": lambda self: None})()
            result = self._run_task()
        self.assertEqual(result, {"retried": 1})
        self.db.refresh(delivery)
        self.assertEqual(delivery.status, "success")
        self.assertEqual(delivery.attempt_count, 2)

    def test_attempt_cap_stops_retries(self):
        app = self._app()
        self._delivery(app, attempts=3)
        with patch("httpx.post") as post:
            result = self._run_task()
        self.assertEqual(result, {"retried": 0})
        post.assert_not_called()

    def test_unrotated_secret_marks_failed(self):
        app = self._app(secret=None)  # 密钥未轮换（A hash cannot sign a payload）
        delivery = self._delivery(app)
        with patch("httpx.post") as post:
            result = self._run_task()
        self.assertEqual(result, {"retried": 0})
        post.assert_not_called()
        self.db.refresh(delivery)
        self.assertEqual(delivery.status, "failed")
        self.assertIn("secret must be rotated", delivery.response_body_snippet)

    def test_inactive_app_skipped(self):
        app = self._app(status="suspended")
        self._delivery(app)
        with patch("httpx.post") as post:
            result = self._run_task()
        self.assertEqual(result, {"retried": 0})
        post.assert_not_called()

    def test_failure_keeps_retryable_and_redacts_snippet(self):
        app = self._app()
        delivery = self._delivery(app)
        with patch("httpx.post", side_effect=ConnectionError("boom")), patch(
            "app.core.external_resilience.external_resilience.call", side_effect=ConnectionError("boom")
        ):
            result = self._run_task()
        self.assertEqual(result, {"retried": 0})
        self.db.refresh(delivery)
        self.assertEqual(delivery.status, "pending")  # 未达上限，保留可重试
        self.assertEqual(delivery.attempt_count, 1)
        self.assertIsNotNone(delivery.response_body_snippet)  # 仅脱敏摘要

    def test_event_id_deduplication(self):
        """同 event_id 幂等：唯一约束保证不重复投递（DB 层）。"""
        from sqlalchemy.exc import IntegrityError

        app = self._app()
        self._delivery(app, event_id="evt-same")
        with self.assertRaises(IntegrityError):
            self._delivery(app, event_id="evt-same")


if __name__ == "__main__":
    unittest.main()
