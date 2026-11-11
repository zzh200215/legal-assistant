"""请假到期提醒：提前 2 天提醒本人、按请假时区、按结束日期去重、到期清空、不转给代理。"""

import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import hash_password
from app.core.database import Base
from app.models.email import OutboundEmailPolicy
from app.models.legal_notifications import (
    LegalNotificationEvent,
    LegalNotificationLeave,
    LegalNotificationPreference,
)
from app.models.org import Organization
from app.models.user import User
from app.services.jobs.delegate_reminders import (
    LEAVE_TEMPLATE_KEY,
    scan_delegate_expiring,
)

NOW = datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)  # 上海 11-10 18:00


class LeaveExpiryReminderTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        self.db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        self.org = Organization(name="请假到期律所", code="leave_expiry")
        self.db.add(self.org)
        self.db.flush()
        self.user = self._user("leave-expiry-user", "原律师")
        self.delegate = self._user("leave-expiry-delegate", "代理律师")
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

    def _leave(self, *, end=date(2026, 11, 12), delegate=True, note="出差"):
        row = LegalNotificationLeave(
            user_id=self.user.id,
            organization_id=self.org.id,
            delegate_user_id=self.delegate.id if delegate else None,
            leave_start=date(2026, 11, 1),
            leave_end=end,
            note=note,
        )
        self.db.add(row)
        self.db.commit()
        return row

    def _pref(self, *, end=date(2026, 11, 12), timezone_name="Asia/Shanghai"):
        row = LegalNotificationPreference(
            user_id=self.user.id,
            organization_id=self.org.id,
            event_type="all",
            channels_json='["site","email"]',
            timezone=timezone_name,
        )
        self.db.add(row)
        self.db.commit()
        return row

    def _reminders(self, channel=None):
        query = self.db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.template_key == LEAVE_TEMPLATE_KEY)
        if channel:
            query = query.filter(LegalNotificationEvent.channel == channel)
        return query.all()

    def test_reminds_two_days_before_end(self):
        self._leave(end=date(2026, 11, 12))

        result = scan_delegate_expiring(self.db, now=NOW)

        self.assertEqual(result["leave_created"], 1)
        self.assertEqual(result["leave_scanned"], 1)
        self.assertEqual(result["leave_cleared"], 0)
        site = self._reminders("site")
        self.assertEqual(len(site), 1)
        event = site[0]
        self.assertEqual(event.user_id, self.user.id)
        self.assertIsNone(event.on_behalf_of_user_id)
        self.assertEqual(event.reference_type, "notification_leave")
        self.assertTrue(event.title.startswith("请假代理将于 2026-11-12 到期"))
        self.assertIn("2 天后", event.body)
        self.assertIn("代理律师", event.body)
        self.assertEqual(
            self.db.query(LegalNotificationEvent)
            .filter(LegalNotificationEvent.user_id == self.delegate.id)
            .count(),
            0,
        )

    def test_reminds_on_end_day_and_tomorrow(self):
        for end, label in ((date(2026, 11, 10), "今天"), (date(2026, 11, 11), "明天")):
            self.db.query(LegalNotificationEvent).delete()
            self.db.query(LegalNotificationLeave).delete()
            self.db.commit()
            self._leave(end=end)
            scan_delegate_expiring(self.db, now=NOW)
            self.assertIn(label, self._reminders("site")[0].body)

    def test_does_not_remind_beyond_two_days(self):
        self._leave(end=date(2026, 11, 13))

        result = scan_delegate_expiring(self.db, now=NOW)

        self.assertEqual(result["leave_created"], 0)
        self.assertEqual(self._reminders(), [])

    def test_expired_leave_is_cleared(self):
        leave = self._leave(end=date(2026, 11, 9), note="出差")

        result = scan_delegate_expiring(self.db, now=NOW)

        self.assertEqual(result["leave_created"], 0)
        self.assertEqual(result["leave_cleared"], 1)
        self.db.refresh(leave)
        self.assertIsNone(leave.delegate_user_id)
        self.assertIsNone(leave.leave_start)
        self.assertIsNone(leave.leave_end)
        self.assertIsNone(leave.note)

    def test_cleanup_is_idempotent(self):
        self._leave(end=date(2026, 11, 9))
        scan_delegate_expiring(self.db, now=NOW)

        second = scan_delegate_expiring(self.db, now=NOW)

        self.assertEqual(second["leave_cleared"], 0)
        self.assertEqual(second["leave_scanned"], 0)

    def test_leave_without_end_is_skipped(self):
        self._leave(delegate=True, end=None)

        result = scan_delegate_expiring(self.db, now=NOW)

        self.assertEqual(result["leave_scanned"], 0)
        self.assertEqual(result["leave_created"], 0)
        self.assertEqual(self._reminders(), [])

    def test_pref_and_leave_are_scanned_together(self):
        self._pref(end=date(2026, 11, 12))
        self.db.query(LegalNotificationPreference).update(
            {"delegate_user_id": self.delegate.id, "delegate_start": date(2026, 11, 1),
             "delegate_end": date(2026, 11, 12)}
        )
        self.db.commit()
        self._leave(end=date(2026, 11, 12))

        result = scan_delegate_expiring(self.db, now=NOW)

        self.assertEqual(result["created"], 1)
        self.assertEqual(result["leave_created"], 1)
        self.assertEqual(len(self.db.query(LegalNotificationEvent).all()), 4)  # 各 site+email

    def test_leave_uses_default_preference_timezone(self):
        """纽约还是 11-10，请假结束日 11-10 应提醒且不清理；UTC 已是 11-11。"""
        self._pref(timezone_name="America/New_York")
        leave = self._leave(end=date(2026, 11, 10))
        now = datetime(2026, 11, 11, 2, 0, tzinfo=timezone.utc)

        result = scan_delegate_expiring(self.db, now=now)

        self.assertEqual(result["leave_created"], 1)
        self.assertEqual(result["leave_cleared"], 0)
        self.db.refresh(leave)
        self.assertIsNotNone(leave.delegate_user_id)
        self.assertIn("今天", self._reminders("site")[0].body)

    def test_repeat_scan_does_not_duplicate(self):
        self._leave(end=date(2026, 11, 12))

        first = scan_delegate_expiring(self.db, now=NOW)
        second = scan_delegate_expiring(self.db, now=NOW)

        self.assertEqual(first["leave_created"], 1)
        self.assertEqual(second["leave_created"], 0)
        self.assertEqual(len(self._reminders()), 2)  # site + email

    def test_extended_leave_end_sends_new_reminder(self):
        leave = self._leave(end=date(2026, 11, 10))
        scan_delegate_expiring(self.db, now=NOW)

        leave.leave_end = date(2026, 11, 12)
        self.db.commit()
        result = scan_delegate_expiring(self.db, now=NOW)

        self.assertEqual(result["leave_created"], 1)
        titles = " ".join(event.title for event in self._reminders())
        self.assertIn("2026-11-10", titles)
        self.assertIn("2026-11-12", titles)

    def test_reminder_is_not_delegated(self):
        self._leave(end=date(2026, 11, 12))
        # 原收件人自己也配了按事件类型的代理：提醒仍只发给本人
        self._pref()
        self.db.query(LegalNotificationPreference).update(
            {"delegate_user_id": self.delegate.id, "event_type": "deadline"}
        )
        self.db.commit()

        scan_delegate_expiring(self.db, now=NOW)

        self.assertEqual({event.user_id for event in self._reminders()}, {self.user.id})
        self.assertTrue(all(event.on_behalf_of_user_id is None for event in self._reminders()))

    def test_quiet_hours_delays_leave_email(self):
        self._pref(timezone_name="Asia/Shanghai")
        self.db.query(LegalNotificationPreference).update(
            {"mute_start": "22:00", "mute_end": "07:00"}
        )
        self.db.commit()
        self._leave(end=date(2026, 11, 12))
        quiet_now = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)  # 上海 23:00

        with patch("app.services.notification.notification_service.utc_now",
                   return_value=quiet_now):
            scan_delegate_expiring(self.db, now=quiet_now)

        email = self._reminders("email")
        self.assertEqual(len(email), 1)
        self.assertIsNotNone(email[0].scheduled_at)
        self.assertIn("静默时段", email[0].body)


if __name__ == "__main__":
    unittest.main()
