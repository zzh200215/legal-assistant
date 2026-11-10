"""静默时段接入普通通知：站内即时、外部渠道延迟到窗口结束，升级可穿透。"""

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import hash_password
from app.core.database import Base
from app.models.email import OutboundEmailPolicy
from app.models.legal_notifications import LegalNotificationEvent, LegalNotificationPreference
from app.models.org import Organization
from app.models.task import Task
from app.models.user import User
from app.services.jobs.task_reminders import scan_task_due_reminders
from app.services.notification.notification_service import notification_service

SHANGHAI_2300 = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)  # 上海 11-10 23:00
SHANGHAI_NOON = datetime(2026, 11, 10, 4, 0, tzinfo=timezone.utc)   # 上海 11-10 12:00
SHANGHAI_0100 = datetime(2026, 11, 10, 17, 0, tzinfo=timezone.utc)  # 上海 11-11 01:00
SERVICE_NOW = "app.services.notification.notification_service.utc_now"


class QuietHoursDeliveryTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        self.db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        self.org = Organization(name="静默测试律所", code="quiet_firm")
        self.db.add(self.org)
        self.db.flush()
        self.user = User(
            username="quiet-user",
            email="quiet-user@example.com",
            hashed_password=hash_password("pw"),
            organization_id=self.org.id,
        )
        self.db.add(self.user)
        self.db.commit()
        self.db.add(OutboundEmailPolicy(organization_id=self.org.id, enabled=True))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _pref(self, start="22:00", end="07:00", tz="Asia/Shanghai", event_type="deadline"):
        row = LegalNotificationPreference(
            user_id=self.user.id,
            organization_id=self.org.id,
            event_type=event_type,
            channels_json='["site","email"]',
            mute_start=start,
            mute_end=end,
            timezone=tz,
        )
        self.db.add(row)
        self.db.commit()
        return row

    def _create(self, *, respect=True, body="任务正文", event_type="deadline"):
        return notification_service.create_multi_channel_notification(
            db=self.db,
            organization_id=self.org.id,
            user_id=self.user.id,
            event_type=event_type,
            title="测试通知",
            body=body,
            channels=["site", "email"],
            respect_quiet_hours=respect,
        )

    def _by_channel(self, events):
        return {event.channel: event for event in events}

    def test_quiet_window_delays_email_but_not_site(self):
        self._pref()

        with patch(SERVICE_NOW, return_value=SHANGHAI_2300):
            events = self._create()

        by_channel = self._by_channel(events)
        self.assertEqual(by_channel["site"].status, "delivered")
        self.assertIsNone(by_channel["site"].scheduled_at)
        self.assertEqual(by_channel["email"].status, "pending")
        self.assertEqual(
            by_channel["email"].scheduled_at.replace(tzinfo=timezone.utc),
            datetime(2026, 11, 10, 23, 0, tzinfo=timezone.utc),
        )
        self.assertIn("静默时段", by_channel["email"].body)
        self.assertIn("07:00", by_channel["email"].body)
        self.assertEqual(by_channel["site"].body, by_channel["email"].body)

    def test_outside_window_no_delay(self):
        self._pref()

        with patch(SERVICE_NOW, return_value=SHANGHAI_NOON):
            events = self._create()

        email = self._by_channel(events)["email"]
        self.assertIsNone(email.scheduled_at)
        self.assertNotIn("静默时段", email.body)

    def test_after_midnight_ends_same_local_day(self):
        self._pref()

        with patch(SERVICE_NOW, return_value=SHANGHAI_0100):
            events = self._create()

        self.assertEqual(
            self._by_channel(events)["email"].scheduled_at.replace(tzinfo=timezone.utc),
            datetime(2026, 11, 10, 23, 0, tzinfo=timezone.utc),
        )

    def test_timezone_controls_window(self):
        self._pref(tz="America/New_York")
        now = datetime(2026, 11, 11, 3, 0, tzinfo=timezone.utc)  # 纽约 11-10 22:00 EST

        with patch(SERVICE_NOW, return_value=now):
            events = self._create()

        self.assertEqual(
            self._by_channel(events)["email"].scheduled_at.replace(tzinfo=timezone.utc),
            datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc),  # 纽约 11-11 07:00 EST
        )

    def test_escalation_can_bypass_quiet_hours(self):
        self._pref()

        with patch(SERVICE_NOW, return_value=SHANGHAI_2300):
            events = self._create(respect=False)

        email = self._by_channel(events)["email"]
        self.assertIsNone(email.scheduled_at)
        self.assertNotIn("静默时段", email.body)

    def test_no_preference_no_delay(self):
        with patch(SERVICE_NOW, return_value=SHANGHAI_2300):
            events = self._create()

        self.assertIsNone(self._by_channel(events)["email"].scheduled_at)

    def test_start_equals_end_is_not_quiet(self):
        self._pref(start="22:00", end="22:00")

        with patch(SERVICE_NOW, return_value=SHANGHAI_2300):
            events = self._create()

        self.assertIsNone(self._by_channel(events)["email"].scheduled_at)

    def test_invalid_timezone_falls_back_to_utc(self):
        self._pref(tz="Mars/Olympus")
        now = datetime(2026, 11, 10, 23, 0, tzinfo=timezone.utc)

        with patch(SERVICE_NOW, return_value=now):
            events = self._create()

        email = self._by_channel(events)["email"]
        self.assertEqual(
            email.scheduled_at.replace(tzinfo=timezone.utc),
            datetime(2026, 11, 11, 7, 0, tzinfo=timezone.utc),
        )
        self.assertIn("07:00", email.body)

    def test_owner_escalation_bypasses_quiet_but_normal_reminder_is_delayed(self):
        """任务升级穿透静默；普通到期提醒的邮件仍然延迟。"""
        owner = self.user
        assignee = User(
            username="quiet-assignee",
            email="quiet-assignee@example.com",
            hashed_password=hash_password("pw"),
            organization_id=self.org.id,
        )
        self.db.add(assignee)
        self.db.commit()
        self.db.add(LegalNotificationPreference(
            user_id=owner.id,
            organization_id=self.org.id,
            event_type="deadline",
            channels_json='["site","email"]',
            mute_start="22:00",
            mute_end="07:00",
            timezone="Asia/Shanghai",
        ))
        task = Task(
            user_id=owner.id,
            organization_id=self.org.id,
            title="静默升级任务",
            status="todo",
            priority="high",
            progress=0,
            due_date=datetime(2026, 11, 7, 23, 59, 59),
            assignee_user_id=assignee.id,
        )
        self.db.add(task)
        self.db.commit()

        with patch("app.services.jobs.task_reminders.utc_now", return_value=SHANGHAI_2300), \
                patch(SERVICE_NOW, return_value=SHANGHAI_2300):
            result = scan_task_due_reminders(self.db)

        self.assertEqual(result["escalated"], 1)
        owner_email = (
            self.db.query(LegalNotificationEvent)
            .filter(
                LegalNotificationEvent.user_id == owner.id,
                LegalNotificationEvent.channel == "email",
                LegalNotificationEvent.reference_id == task.id,
            )
            .one()
        )
        self.assertIsNone(owner_email.scheduled_at)
        self.assertNotIn("静默时段", owner_email.body)


if __name__ == "__main__":
    unittest.main()
