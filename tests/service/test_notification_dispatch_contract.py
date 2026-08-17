"""Service 层：notification_service 领取（claim）语义与投递分支补测。

覆盖 app/services/notification/notification_service.py：
- _claim_events：SQL keyset claim 边界（scheduled_at / next_retry_at / claim 租约 /
  email 未建 send request 才领取）；
- dispatch_pending：批次统计聚合；
- _dispatch_event：站内投递、未知渠道死信、投递异常标记 failed；
- _dispatch_email：无邮箱死信、已有 send request 回退等待态。
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
from app.models.legal_notifications import LegalNotificationEvent
from app.models.org import Organization
from app.models.user import User
from app.services.notification.notification_service import (
    CHANNEL_EMAIL,
    CHANNEL_SITE,
    STATUS_APPROVED,
    STATUS_DEAD_LETTER,
    STATUS_DELIVERED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SENDING,
    notification_service,
)


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


class NotificationDispatchContractTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()
        self.org = Organization(name="NotifyOrg", code="NFC")
        self.db.add(self.org)
        self.db.commit()
        self.user = User(username="nfc", email="nfc@example.com", hashed_password="h",
                         organization_id=self.org.id)
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _event(self, **kw) -> LegalNotificationEvent:
        fields = {
            "organization_id": self.org.id, "user_id": self.user.id,
            "event_type": "deadline_reminder", "title": "提醒", "body": "b",
            "channel": CHANNEL_SITE, "status": STATUS_PENDING,
        }
        fields.update(kw)
        event = LegalNotificationEvent(**fields)
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        return event

    # ── _claim_events 边界 ──────────────────────────────────────────────────
    def test_claim_skips_future_scheduled(self):
        self._event(scheduled_at=utc_now() + timedelta(hours=1))
        now = utc_now()
        claimed = notification_service._claim_events(self.db, "worker-1", now, 10)
        self.assertEqual(claimed, [])

    def test_claim_skips_future_retry(self):
        self._event(status=STATUS_FAILED, next_retry_at=utc_now() + timedelta(minutes=5))
        claimed = notification_service._claim_events(self.db, "worker-1", utc_now(), 10)
        self.assertEqual(claimed, [])

    def test_claim_skips_live_lease(self):
        self._event(status=STATUS_SENDING, claimed_by="other",
                    claim_expires_at=utc_now() + timedelta(minutes=5))
        claimed = notification_service._claim_events(self.db, "worker-1", utc_now(), 10)
        self.assertEqual(claimed, [])

    def test_claim_takes_pending_and_approved(self):
        pending = self._event(channel=CHANNEL_SITE)
        approved = self._event(channel=CHANNEL_SITE, status=STATUS_APPROVED)
        claimed = notification_service._claim_events(self.db, "worker-1", utc_now(), 10)
        self.assertEqual({e.id for e in claimed}, {pending.id, approved.id})
        self.db.refresh(pending)
        self.db.refresh(approved)
        self.assertEqual(pending.status, STATUS_SENDING)
        self.assertEqual(approved.status, STATUS_SENDING)
        self.assertEqual(pending.claimed_by, "worker-1")

    def test_claim_respects_batch_limit(self):
        for _ in range(5):
            self._event(channel=CHANNEL_SITE)
        claimed = notification_service._claim_events(self.db, "worker-1", utc_now(), 2)
        self.assertEqual(len(claimed), 2)

    # ── dispatch_pending 聚合 ───────────────────────────────────────────────
    def test_dispatch_pending_delivers_site_events(self):
        self._event(channel=CHANNEL_SITE)
        self._event(channel=CHANNEL_SITE)
        stats = notification_service.dispatch_pending(db=self.db)
        self.assertEqual(stats["delivered"], 2)
        self.assertEqual(self.db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.status == STATUS_DELIVERED).count(), 2)

    # ── _dispatch_event 分支 ────────────────────────────────────────────────
    def test_dispatch_unknown_channel_goes_dead_letter(self):
        event = self._event(channel="fax")
        result = notification_service._dispatch_event(self.db, event)
        self.assertEqual(result, "failed")
        self.db.refresh(event)
        self.assertEqual(event.status, STATUS_DEAD_LETTER)

    def test_dispatch_exception_marks_failed(self):
        event = self._event(channel=CHANNEL_SITE)
        with patch.object(notification_service, "_dispatch_site", side_effect=RuntimeError("boom")):
            result = notification_service._dispatch_event(self.db, event)
        self.assertEqual(result, "failed")
        self.db.commit()  # mark_failed 未提交（直调场景），显式提交后核对
        self.db.refresh(event)
        self.assertEqual(event.status, STATUS_FAILED)
        self.assertEqual(event.error_code, "RuntimeError")

    def test_dispatch_email_without_send_request_and_no_user_email(self):
        no_email_user = User(username="noemail", email="", hashed_password="h",
                             organization_id=self.org.id)
        self.db.add(no_email_user)
        self.db.commit()
        self.db.refresh(no_email_user)
        event = self._event(channel=CHANNEL_EMAIL, user_id=no_email_user.id)
        result = notification_service._dispatch_event(self.db, event)
        self.assertEqual(result, "failed")
        self.db.commit()  # mark_dead_letter 未提交（直调场景），显式提交后核对
        self.db.refresh(event)
        self.assertEqual(event.status, STATUS_DEAD_LETTER)

    def test_dispatch_email_with_existing_send_request_skips(self):
        event = self._event(channel=CHANNEL_EMAIL, email_send_request_id=5)
        result = notification_service._dispatch_event(self.db, event)
        self.assertEqual(result, "skipped")


if __name__ == "__main__":
    unittest.main()
