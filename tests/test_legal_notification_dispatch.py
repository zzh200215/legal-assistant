"""补齐通知中心缺口：pending 通知事件需被调度投递（站内→delivered），进入铃铛未读。"""
import unittest
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from unittest.mock import patch

import app.models  # noqa: F401
from app.core.auth import hash_password
from app.core.database import Base
from app.core.time import utc_now
from app.models.legal_notifications import LegalNotificationEvent
from app.models.org import Organization
from app.models.user import User, UserStatus


def _make_engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


class NotificationDispatchTests(unittest.TestCase):
    """dispatch_notification_events_task：pending→delivered，铃铛可见"""

    def setUp(self):
        self.engine = _make_engine()
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.SessionLocal = Session
        Base.metadata.create_all(bind=self.engine)
        self.db = Session()

        org = Organization(name="DispatchOrg", code="DISP")
        self.db.add(org)
        self.db.flush()

        self.user = User(
            username="dispatch_user",
            email="dispatch@t.com",
            hashed_password=hash_password("pw"),
            role="user",
            status=UserStatus.active.value,
            organization_id=org.id,
        )
        self.db.add(self.user)
        self.db.commit()

        self.org_id = org.id
        self.user_id = self.user.id

    def tearDown(self):
        self.db.close()

    def _pending_event(self, *, status: str = "pending", channel: str = "site",
                       scheduled_at=None) -> LegalNotificationEvent:
        ev = LegalNotificationEvent(
            organization_id=self.org_id,
            user_id=self.user_id,
            case_id=None,
            event_type="deadline_reminder",
            title="关键日期提醒",
            channel=channel,
            status=status,
            scheduled_at=scheduled_at,
        )
        self.db.add(ev)
        self.db.commit()
        self.db.refresh(ev)
        return ev

    def _run_dispatch(self):
        from app.tasks import dispatch_notification_events_task
        with patch("app.tasks.notification_tasks.SessionLocal", self.SessionLocal):
            return dispatch_notification_events_task()

    def test_pending_site_event_becomes_delivered(self):
        ev = self._pending_event(scheduled_at=utc_now() - timedelta(minutes=5))
        self._run_dispatch()
        self.db.refresh(ev)
        self.assertEqual(ev.status, "delivered")
        self.assertIsNotNone(ev.sent_at)

    def test_delivered_event_counts_as_unread_for_bell(self):
        ev = self._pending_event(scheduled_at=utc_now() - timedelta(minutes=5))
        self._run_dispatch()
        from app.services.notification.notification_service import notification_service
        count = notification_service.get_unread_count(db=self.db, user_id=self.user_id)
        self.assertEqual(count, 1)

    def test_future_scheduled_event_not_dispatched(self):
        ev = self._pending_event(scheduled_at=utc_now() + timedelta(hours=1))
        self._run_dispatch()
        self.db.refresh(ev)
        self.assertEqual(ev.status, "pending")

    def test_already_delivered_event_left_alone(self):
        ev = self._pending_event(status="delivered", scheduled_at=None)
        self._run_dispatch()
        self.db.refresh(ev)
        self.assertEqual(ev.status, "delivered")

    def test_dispatch_is_isolated_to_pending(self):
        ev = self._pending_event(scheduled_at=utc_now() - timedelta(minutes=5))
        self._run_dispatch()
        self._run_dispatch()
        self.db.refresh(ev)
        self.assertEqual(ev.status, "delivered")


if __name__ == "__main__":
    unittest.main()
