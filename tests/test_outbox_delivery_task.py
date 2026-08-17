"""邮件 Outbox 投递任务测试：worker 并发 claim、重放不重发、死信、人工重试、租约回收。"""
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import hash_password
from app.core.database import Base
from app.core.external_resilience import ExternalError, ExternalErrorKind
from app.core.time import utc_now
from app.models.email import EmailDraft, EmailSendRequest
from app.models.org import Organization
from app.models.user import User, UserStatus
from app.services.notification.outbound_email_service import outbound_email_service


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout=20):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def ehlo(self):
        return None

    def starttls(self):
        return None

    def login(self, username, password):
        pass

    def send_message(self, message):
        FakeSMTP.sent.append(message)


class EmailOutboxDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.SessionLocal()
        org = Organization(name="DeliveryOrg", code="DLVR")
        self.db.add(org)
        self.db.flush()
        self.org_id = org.id
        self.user = User(username="sender", email="sender@example.com",
                         hashed_password=hash_password("pw"), role="user",
                         status=UserStatus.active.value, organization_id=org.id)
        self.db.add(self.user)
        self.db.flush()
        self.user_id = self.user.id
        self.approver = User(username="approver", email="approver@example.com",
                             hashed_password=hash_password("pw"), role="admin",
                             status=UserStatus.active.value, organization_id=org.id)
        self.db.add(self.approver)
        self.db.commit()

        outbound_email_service.update_policy(
            db=self.db, user=self.user,
            request=SimpleNamespace(enabled=True, allowed_recipient_domains=["example.com"],
                                    max_sends_per_hour=100, require_approval=True,
                                    dlp_enabled=True, dlp_action="block"),
        )
        self.connector = outbound_email_service.create_smtp_connector(
            db=self.db, user=self.user,
            request=SimpleNamespace(name="smtp", host="smtp.example.com", port=587,
                                    username="sender@example.com", password="pw",
                                    from_address="sender@example.com", use_starttls=True),
        )
        FakeSMTP.sent = []

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _approved_request(self) -> EmailSendRequest:
        draft = EmailDraft(user_id=self.user_id, organization_id=self.org_id,
                           subject="项目同步", recipient="team@example.com",
                           content="请确认本周进度。", status="draft",
                           generation_type="generate", tone="professional")
        self.db.add(draft)
        self.db.commit()
        self.db.refresh(draft)
        request = outbound_email_service.request_send(draft.id, connector_id=self.connector.id,
                                                      db=self.db, user=self.user)
        outbound_email_service.decide_request(request.id, approved=True, note=None,
                                              db=self.db, user=self.approver)
        self.db.refresh(request)
        return request

    def test_worker_replay_no_duplicate_send(self):
        request = self._approved_request()
        with patch("app.services.notification.outbound_email_service.smtplib.SMTP", FakeSMTP):
            owner = "worker-1"
            batch = outbound_email_service.claim_pending_batch(db=self.db, owner=owner)
            self.assertEqual(len(batch), 1)
            for req in batch:
                outbound_email_service._perform_send(db=self.db, request=req, owner=owner)
            self.db.commit()
        self.assertEqual(len(FakeSMTP.sent), 1)
        # worker 重放：已 sent 不再投递
        with patch("app.services.notification.outbound_email_service.smtplib.SMTP", FakeSMTP):
            batch2 = outbound_email_service.claim_pending_batch(db=self.db, owner="worker-2")
        self.assertEqual(len(batch2), 0)
        self.assertEqual(len(FakeSMTP.sent), 1)
        self.db.refresh(request)
        self.assertEqual(request.status, "sent")

    def test_concurrent_claim_is_disjoint(self):
        for _ in range(4):
            self._approved_request()
        a = outbound_email_service.claim_pending_batch(db=self.db, owner="worker-a")
        b = outbound_email_service.claim_pending_batch(db=self.db, owner="worker-b")
        a_ids = {r.id for r in a}
        b_ids = {r.id for r in b}
        self.assertTrue(a_ids.isdisjoint(b_ids))
        self.assertEqual(len(a) + len(b), 4)

    def test_retryable_exhaustion_goes_to_dead_letter(self):
        request = self._approved_request()
        request.max_attempts = 1
        self.db.commit()

        def _fail(_fn, **_kw):
            raise ExternalError(kind=ExternalErrorKind.SERVER_5XX, message="5xx", status_code=500)

        with patch("app.services.notification.outbound_email_service.external_resilience.call", side_effect=_fail):
            owner = "worker-1"
            batch = outbound_email_service.claim_pending_batch(db=self.db, owner=owner)
            for req in batch:
                with self.assertRaises(ExternalError):
                    outbound_email_service._perform_send(db=self.db, request=req, owner=owner)
                self.db.rollback()
        self.db.refresh(request)
        self.assertEqual(request.status, "dead_letter")
        self.assertIsNotNone(request.dead_letter_at)
        self.assertEqual(request.attempt, 1)
        self.assertEqual(request.error_code, "server_5xx")

    def test_retryable_failure_schedules_retry(self):
        request = self._approved_request()
        request.max_attempts = 3
        self.db.commit()

        def _fail(_fn, **_kw):
            raise ExternalError(kind=ExternalErrorKind.SERVER_5XX, message="5xx", status_code=500)

        with patch("app.services.notification.outbound_email_service.external_resilience.call", side_effect=_fail):
            owner = "worker-1"
            batch = outbound_email_service.claim_pending_batch(db=self.db, owner=owner)
            for req in batch:
                with self.assertRaises(ExternalError):
                    outbound_email_service._perform_send(db=self.db, request=req, owner=owner)
                self.db.rollback()
        self.db.refresh(request)
        self.assertEqual(request.status, "failed")
        self.assertIsNotNone(request.next_retry_at)
        self.assertIsNotNone(request.error_code)

    def test_non_retryable_error_immediate_dead_letter(self):
        request = self._approved_request()
        request.max_attempts = 3
        self.db.commit()

        def _fail(_fn, **_kw):
            raise ExternalError(kind=ExternalErrorKind.AUTH, message="auth failed", status_code=401)

        with patch("app.services.notification.outbound_email_service.external_resilience.call", side_effect=_fail):
            owner = "worker-1"
            batch = outbound_email_service.claim_pending_batch(db=self.db, owner=owner)
            for req in batch:
                with self.assertRaises(ExternalError):
                    outbound_email_service._perform_send(db=self.db, request=req, owner=owner)
                self.db.rollback()
        self.db.refresh(request)
        self.assertEqual(request.status, "dead_letter")
        self.assertEqual(request.error_code, "auth")

    def test_worker_recovery_reclaims_stale_claim(self):
        request = self._approved_request()
        # 模拟 worker 崩溃：已 claim 且租约远超 TTL 过期
        request.status = "sending"
        request.claimed_by = "dead-worker"
        request.claim_expires_at = utc_now() - timedelta(seconds=1000)
        self.db.commit()
        reclaimed = outbound_email_service.reclaim_stale(db=self.db)
        self.assertEqual(reclaimed, 1)
        self.db.refresh(request)
        self.assertEqual(request.status, "failed")
        self.assertEqual(request.error_code, "LEASE_EXPIRED")
        # 重新领取可投递
        with patch("app.services.notification.outbound_email_service.smtplib.SMTP", FakeSMTP):
            batch = outbound_email_service.claim_pending_batch(db=self.db, owner="worker-2")
            self.assertEqual(len(batch), 1)
            for req in batch:
                outbound_email_service._perform_send(db=self.db, request=req, owner="worker-2")
            self.db.commit()
        self.assertEqual(len(FakeSMTP.sent), 1)

    def test_manual_retry_dead_letter_with_permission_and_idempotency(self):
        request = self._approved_request()
        request.max_attempts = 1
        request.status = "dead_letter"
        request.dead_letter_at = utc_now()
        request.dead_letter_reason = "test"
        original_key = request.idempotency_key
        self.db.commit()

        outsider = User(username="outsider", email="o@example.com",
                        hashed_password=hash_password("pw"), role="user",
                        status=UserStatus.active.value, organization_id=self.org_id)
        self.db.add(outsider)
        self.db.commit()
        with self.assertRaises(ValueError):
            outbound_email_service.manual_retry(request.id, db=self.db, user=outsider)
        retried = outbound_email_service.manual_retry(request.id, db=self.db, user=self.approver)
        self.assertEqual(retried.status, "pending")
        self.assertEqual(retried.idempotency_key, original_key, "人工重试必须保留原幂等键")
        self.assertIsNone(retried.dead_letter_at)
        self.assertEqual(retried.attempt, 0)

    def test_task_wrapper_delivers_approved(self):
        request = self._approved_request()
        with patch("app.services.notification.outbound_email_service.smtplib.SMTP", FakeSMTP):
            from app.tasks import deliver_email_send_requests_task
            with patch("app.tasks.notification_tasks.SessionLocal", self.SessionLocal):
                result = deliver_email_send_requests_task()
        self.assertEqual(result["delivered"], 1)
        self.db.refresh(request)
        self.assertEqual(request.status, "sent")


if __name__ == "__main__":
    unittest.main()
