"""代理到期提醒：提前 2 天提醒原收件人，按当地日期、按结束日期去重、不转给代理。"""

import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import hash_password
from app.core.celery_app import celery_app
from app.core.database import Base
from app.models.email import OutboundEmailPolicy
from app.models.legal_notifications import LegalNotificationEvent, LegalNotificationPreference
from app.models.org import Organization
from app.models.user import User
from app.services.jobs.delegate_reminders import TEMPLATE_KEY, scan_delegate_expiring

NOW = datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)  # 上海 11-10 18:00


class DelegateExpiryReminderTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        self.db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        self.org = Organization(name="代理到期律所", code="delegate_expiry")
        self.db.add(self.org)
        self.db.flush()
        self.user = self._user("expiry-user", "原律师")
        self.delegate = self._user("expiry-delegate", "代理律师")
        self.db.commit()
        self.db.add(OutboundEmailPolicy(organization_id=self.org.id, enabled=True))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _user(self, username, full_name):
        user = User(
            username=username,
            email=f"{username}@example.com",
            hashed_password=hash_password("pw"),
            full_name=full_name,
            organization_id=self.org.id,
            status="active",
        )
        self.db.add(user)
        self.db.flush()
        return user

    def _pref(self, *, end=date(2026, 11, 12), delegate=True,
              timezone_name="Asia/Shanghai", mute_start=None, mute_end=None):
        row = LegalNotificationPreference(
            user_id=self.user.id,
            organization_id=self.org.id,
            event_type="deadline",
            channels_json='["site","email"]',
            delegate_user_id=self.delegate.id if delegate else None,
            delegate_start=date(2026, 11, 1),
            delegate_end=end,
            timezone=timezone_name,
            mute_start=mute_start,
            mute_end=mute_end,
        )
        self.db.add(row)
        self.db.commit()
        return row

    def _events(self, user=None):
        query = self.db.query(LegalNotificationEvent)
        if user is not None:
            query = query.filter(LegalNotificationEvent.user_id == user.id)
        return query.all()

    def _reminder(self):
        return (
            self.db.query(LegalNotificationEvent)
            .filter(
                LegalNotificationEvent.template_key == TEMPLATE_KEY,
                LegalNotificationEvent.channel == "site",
            )
            .one()
        )

    def test_reminds_two_days_before_end(self):
        self._pref(end=date(2026, 11, 12))

        result = scan_delegate_expiring(self.db, now=NOW)

        self.assertEqual(result, {"created": 1, "scanned": 1, "cleared": 0,
                                  "leave_created": 0, "leave_scanned": 0,
                                  "leave_cleared": 0, "leave_started": 0,
                                  "leave_ended": 0})
        event = self._reminder()
        self.assertEqual(event.user_id, self.user.id)
        self.assertIsNone(event.on_behalf_of_user_id)
        self.assertEqual(event.reference_type, "notification_preference")
        self.assertIn("2026-11-12", event.title)
        self.assertIn("2 天后", event.body)
        self.assertIn("代理律师", event.body)
        self.assertEqual(self._events(self.delegate), [])

    def test_reminds_on_end_day_and_tomorrow(self):
        for end, label in ((date(2026, 11, 10), "今天"), (date(2026, 11, 11), "明天")):
            self.db.query(LegalNotificationEvent).delete()
            self.db.query(LegalNotificationPreference).delete()
            self.db.commit()
            self._pref(end=end)
            scan_delegate_expiring(self.db, now=NOW)
            self.assertIn(label, self._reminder().body)

    def test_does_not_remind_beyond_two_days(self):
        self._pref(end=date(2026, 11, 13))

        result = scan_delegate_expiring(self.db, now=NOW)

        self.assertEqual(result["created"], 0)
        self.assertEqual(self._events(), [])

    def test_expired_delegate_is_cleared(self):
        pref = self._pref(end=date(2026, 11, 9))

        result = scan_delegate_expiring(self.db, now=NOW)

        self.assertEqual(result["created"], 0)
        self.assertEqual(result["cleared"], 1)
        self.db.refresh(pref)
        self.assertIsNone(pref.delegate_user_id)
        self.assertIsNone(pref.delegate_start)
        self.assertIsNone(pref.delegate_end)

    def test_cleanup_is_idempotent(self):
        self._pref(end=date(2026, 11, 9))
        scan_delegate_expiring(self.db, now=NOW)

        second = scan_delegate_expiring(self.db, now=NOW)

        self.assertEqual(second["cleared"], 0)
        self.assertEqual(second["scanned"], 0)

    def test_cleanup_uses_local_timezone(self):
        """纽约还是 11-10，结束日 11-10 应提醒且不清理；UTC 已是 11-11。"""
        pref = self._pref(end=date(2026, 11, 10), timezone_name="America/New_York")
        now = datetime(2026, 11, 11, 2, 0, tzinfo=timezone.utc)

        result = scan_delegate_expiring(self.db, now=now)

        self.assertEqual(result["created"], 1)
        self.assertEqual(result["cleared"], 0)
        self.db.refresh(pref)
        self.assertIsNotNone(pref.delegate_user_id)
        self.assertIn("今天", self._reminder().body)

    def test_after_cleanup_notification_goes_to_original(self):
        from app.services.notification.notification_service import notification_service

        self._pref(end=date(2026, 11, 9))
        scan_delegate_expiring(self.db, now=NOW)

        events = notification_service.create_multi_channel_notification(
            db=self.db,
            organization_id=self.org.id,
            user_id=self.user.id,
            event_type="deadline",
            title="清理后通知",
            body="正文",
            channels=["site"],
        )

        self.assertEqual({event.user_id for event in events}, {self.user.id})
        self.assertTrue(all(event.on_behalf_of_user_id is None for event in events))

    def test_skips_without_delegate_or_end(self):
        self._pref(delegate=False)
        row = LegalNotificationPreference(
            user_id=self.user.id, organization_id=self.org.id,
            event_type="approval", delegate_user_id=self.delegate.id,
            delegate_end=None, channels_json='["site"]',
        )
        self.db.add(row)
        self.db.commit()

        result = scan_delegate_expiring(self.db, now=NOW)

        self.assertEqual(result, {"created": 0, "scanned": 0, "cleared": 0,
                                  "leave_created": 0, "leave_scanned": 0,
                                  "leave_cleared": 0, "leave_started": 0,
                                  "leave_ended": 0})
        self.assertEqual(self._events(), [])

    def test_repeat_scan_does_not_duplicate(self):
        self._pref(end=date(2026, 11, 12))

        first = scan_delegate_expiring(self.db, now=NOW)
        second = scan_delegate_expiring(self.db, now=NOW)

        self.assertEqual(first["created"], 1)
        self.assertEqual(second["created"], 0)
        self.assertEqual(len(self._events()), 2)  # site + email

    def test_extended_end_date_sends_new_reminder(self):
        pref = self._pref(end=date(2026, 11, 10))
        scan_delegate_expiring(self.db, now=NOW)

        pref.delegate_end = date(2026, 11, 12)
        self.db.commit()
        result = scan_delegate_expiring(self.db, now=NOW)

        self.assertEqual(result["created"], 1)
        titles = {event.title for event in self._events()}
        self.assertIn("2026-11-10", " ".join(titles))
        self.assertIn("2026-11-12", " ".join(titles))

    def test_uses_preference_timezone_date(self):
        # UTC 已是 11-11，纽约仍是 11-10；结束日期 11-11 应显示「明天」
        self._pref(end=date(2026, 11, 11), timezone_name="America/New_York")
        now = datetime(2026, 11, 11, 2, 0, tzinfo=timezone.utc)

        scan_delegate_expiring(self.db, now=now)

        self.assertIn("明天", self._reminder().body)

    def test_quiet_hours_delays_email(self):
        self._pref(
            end=date(2026, 11, 12),
            mute_start="22:00", mute_end="07:00",
        )
        # 上海 11-10 23:00
        now = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)

        with patch("app.services.notification.notification_service.utc_now", return_value=now):
            scan_delegate_expiring(self.db, now=now)

        email = (
            self.db.query(LegalNotificationEvent)
            .filter(
                LegalNotificationEvent.template_key == TEMPLATE_KEY,
                LegalNotificationEvent.channel == "email",
            )
            .one()
        )
        self.assertIsNotNone(email.scheduled_at)
        self.assertIn("静默时段", email.body)

    def test_multiple_event_types_same_end_remind_once(self):
        self._pref(end=date(2026, 11, 12))
        self.db.add(LegalNotificationPreference(
            user_id=self.user.id,
            organization_id=self.org.id,
            event_type="approval",
            channels_json='["site","email"]',
            delegate_user_id=self.delegate.id,
            delegate_start=date(2026, 11, 1),
            delegate_end=date(2026, 11, 12),
            timezone="Asia/Shanghai",
        ))
        self.db.commit()

        result = scan_delegate_expiring(self.db, now=NOW)

        self.assertEqual(result["created"], 1)
        self.assertEqual(len(self._events()), 2)  # site + email

    def test_beat_schedule_registers_daily_task(self):
        entry = celery_app.conf.beat_schedule["check-delegate-expiring"]
        self.assertEqual(entry["task"], "check_delegate_expiring")
        self.assertEqual(entry["schedule"].hour, {9})
        self.assertEqual(entry["schedule"].minute, {10})


if __name__ == "__main__":
    unittest.main()
