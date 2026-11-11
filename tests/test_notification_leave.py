"""全局请假代理：优先级（请假 > 事件类型代理 > 本人）、有效期、无效回退、强提醒穿透。"""

import unittest
from datetime import date, datetime, timedelta, timezone
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
    LegalNotificationPreference,
)
from app.models.org import Organization
from app.models.task import Task
from app.models.user import User
from app.services.jobs.task_reminders import scan_task_due_reminders
from app.services.notification.notification_service import notification_service

NOW = datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)  # 上海 11-10 18:00
SERVICE_NOW = "app.services.notification.notification_service.utc_now"


class NotificationLeaveTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        self.db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        self.org = Organization(name="请假测试律所", code="leave_firm")
        self.other_org = Organization(name="其它律所", code="leave_other")
        self.db.add_all([self.org, self.other_org])
        self.db.flush()
        self.alice = self._user("leave-alice", "Alice", self.org.id)
        self.bob = self._user("leave-bob", "Bob", self.org.id)
        self.carol = self._user("leave-carol", "Carol", self.org.id)
        self.outsider = self._user("leave-outsider", "Outsider", self.other_org.id)
        self.disabled = self._user("leave-disabled", "Disabled", self.org.id, status="disabled")
        self.db.commit()
        self.db.add(OutboundEmailPolicy(organization_id=self.org.id, enabled=True))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _user(self, username, full_name, org_id, status="active"):
        user = User(
            username=username,
            email=f"{username}@example.com",
            hashed_password=hash_password("pw"),
            full_name=full_name,
            organization_id=org_id,
            status=status,
        )
        self.db.add(user)
        self.db.flush()
        return user

    def _pref(self, user, *, delegate=None, event_type="deadline",
              timezone_name="Asia/Shanghai"):
        row = LegalNotificationPreference(
            user_id=user.id,
            organization_id=user.organization_id,
            event_type=event_type,
            channels_json='["site","email"]',
            delegate_user_id=delegate.id if delegate else None,
            timezone=timezone_name,
        )
        self.db.add(row)
        self.db.commit()
        return row

    def _leave(self, user, *, delegate, start=None, end=None, note=None):
        return notification_service.set_leave(
            db=self.db,
            user_id=user.id,
            organization_id=user.organization_id,
            delegate_user_id=delegate.id if delegate else None,
            leave_start=start,
            leave_end=end,
            note=note,
        )

    def _create(self, user, *, title="请假测试通知", event_type="deadline"):
        return notification_service.create_multi_channel_notification(
            db=self.db,
            organization_id=user.organization_id,
            user_id=user.id,
            event_type=event_type,
            title=title,
            body="通知正文",
            channels=["site", "email"],
        )

    def _events(self, user):
        return (
            self.db.query(LegalNotificationEvent)
            .filter(LegalNotificationEvent.user_id == user.id)
            .all()
        )

    def _assert_delegated_to(self, events, delegate, original):
        self.assertEqual({event.user_id for event in events}, {delegate.id})
        self.assertEqual({event.on_behalf_of_user_id for event in events}, {original.id})
        self.assertTrue(all(f"代 {original.full_name} 接收" in event.body for event in events))

    def _assert_original(self, events, user):
        self.assertEqual({event.user_id for event in events}, {user.id})
        self.assertTrue(all(event.on_behalf_of_user_id is None for event in events))

    def test_leave_overrides_event_type_delegate(self):
        self._pref(self.alice, delegate=self.bob)
        self._leave(self.alice, delegate=self.carol)

        with patch(SERVICE_NOW, return_value=NOW):
            events = self._create(self.alice)

        self._assert_delegated_to(events, self.carol, self.alice)
        self.assertEqual(self._events(self.bob), [])

    def test_leave_covers_event_types_without_own_preference(self):
        self._pref(self.alice, delegate=self.bob, event_type="deadline")
        self._leave(self.alice, delegate=self.carol)

        with patch(SERVICE_NOW, return_value=NOW):
            events = self._create(self.alice, event_type="approval")

        self._assert_delegated_to(events, self.carol, self.alice)

    def test_scheduled_leave_falls_back_to_event_type_delegate(self):
        self._pref(self.alice, delegate=self.bob)
        self._leave(self.alice, delegate=self.carol, start=date(2026, 11, 11))

        with patch(SERVICE_NOW, return_value=NOW):
            events = self._create(self.alice)

        self._assert_delegated_to(events, self.bob, self.alice)

    def test_expired_leave_falls_back_to_original(self):
        self._leave(self.alice, delegate=self.carol, end=date(2026, 11, 9))

        with patch(SERVICE_NOW, return_value=NOW):
            events = self._create(self.alice)

        self._assert_original(events, self.alice)

    def test_leave_window_includes_today(self):
        self._leave(
            self.alice, delegate=self.carol,
            start=date(2026, 11, 10), end=date(2026, 11, 10),
        )

        with patch(SERVICE_NOW, return_value=NOW):
            events = self._create(self.alice)

        self._assert_delegated_to(events, self.carol, self.alice)

    def test_start_only_leave_is_active_from_that_day(self):
        self._leave(self.alice, delegate=self.carol, start=date(2026, 11, 10))

        with patch(SERVICE_NOW, return_value=NOW):
            events = self._create(self.alice)

        self._assert_delegated_to(events, self.carol, self.alice)

    def test_end_only_leave_is_active_through_that_day(self):
        self._leave(self.alice, delegate=self.carol, end=date(2026, 11, 10))

        with patch(SERVICE_NOW, return_value=NOW):
            events = self._create(self.alice)

        self._assert_delegated_to(events, self.carol, self.alice)

    def test_disabled_leave_delegate_falls_back_to_event_delegate(self):
        self._pref(self.alice, delegate=self.bob)
        self._leave(self.alice, delegate=self.disabled)

        with patch(SERVICE_NOW, return_value=NOW):
            events = self._create(self.alice)

        self._assert_delegated_to(events, self.bob, self.alice)

    def test_cross_org_leave_delegate_falls_back(self):
        self._leave(self.alice, delegate=self.outsider)

        with patch(SERVICE_NOW, return_value=NOW):
            events = self._create(self.alice)

        self._assert_original(events, self.alice)

    def test_leave_to_self_falls_back(self):
        self._leave(self.alice, delegate=self.alice)

        with patch(SERVICE_NOW, return_value=NOW):
            events = self._create(self.alice)

        self._assert_original(events, self.alice)

    def test_leave_window_uses_default_preference_timezone(self):
        """UTC 已是 11-11，纽约还是 11-10；请假 11-11 开始尚未生效。"""
        self._pref(self.alice, event_type="all", timezone_name="America/New_York")
        self._leave(self.alice, delegate=self.carol, start=date(2026, 11, 11))
        ny_late = datetime(2026, 11, 11, 2, 0, tzinfo=timezone.utc)

        with patch(SERVICE_NOW, return_value=ny_late):
            events = self._create(self.alice)

        self._assert_original(events, self.alice)

    def test_user_timezone_falls_back_to_shanghai(self):
        self.assertEqual(
            notification_service.user_timezone(
                db=self.db, user_id=self.alice.id, organization_id=self.org.id,
            ),
            "Asia/Shanghai",
        )
        self._pref(self.alice, event_type="all", timezone_name="UTC")
        self.assertEqual(
            notification_service.user_timezone(
                db=self.db, user_id=self.alice.id, organization_id=self.org.id,
            ),
            "UTC",
        )

    def test_two_originals_same_leave_delegate_are_not_deduped(self):
        self._leave(self.alice, delegate=self.carol)
        self._leave(self.bob, delegate=self.carol)

        with patch(SERVICE_NOW, return_value=NOW):
            first = self._create(self.alice, title="同标题通知")
            second = self._create(self.bob, title="同标题通知")

        self.assertEqual(len(first), 2)
        self.assertEqual(len(second), 2)
        originals = {event.on_behalf_of_user_id for event in self._events(self.carol)}
        self.assertEqual(originals, {self.alice.id, self.bob.id})

    def test_clear_leave_restores_original_recipient(self):
        self._leave(self.alice, delegate=self.carol, note="出差")

        self.assertTrue(notification_service.clear_leave(
            db=self.db, user_id=self.alice.id, organization_id=self.org.id,
        ))
        with patch(SERVICE_NOW, return_value=NOW):
            events = self._create(self.alice)

        self._assert_original(events, self.alice)

    def test_set_leave_without_delegate_clears_window_and_note(self):
        self._leave(
            self.alice, delegate=self.carol,
            start=date(2026, 11, 10), end=date(2026, 11, 20), note="婚假",
        )

        leave = self._leave(self.alice, delegate=None)

        self.assertIsNone(leave.delegate_user_id)
        self.assertIsNone(leave.leave_start)
        self.assertIsNone(leave.leave_end)
        self.assertIsNone(leave.note)

    def test_task_reminder_uses_leave_delegate(self):
        self._leave(self.alice, delegate=self.carol)
        task = Task(
            user_id=self.alice.id,
            organization_id=self.org.id,
            title="请假期间的任务提醒",
            status="todo",
            priority="medium",
            progress=0,
            due_date=datetime(2026, 11, 10, 23, 59, 59),
            assignee_user_id=self.alice.id,
        )
        self.db.add(task)
        self.db.commit()

        with patch("app.services.jobs.task_reminders.utc_now", return_value=NOW), \
                patch(SERVICE_NOW, return_value=NOW):
            result = scan_task_due_reminders(self.db)

        self.assertEqual(result["created"], 1)
        events = self._events(self.carol)
        self.assertEqual(len(events), 2)  # site + email
        self.assertTrue(all(event.on_behalf_of_user_id == self.alice.id for event in events))

    def test_task_escalation_not_delegated_by_leave(self):
        self._leave(self.alice, delegate=self.carol)
        task = Task(
            user_id=self.alice.id,
            organization_id=self.org.id,
            title="请假不拦升级",
            status="todo",
            priority="high",
            progress=0,
            due_date=datetime(2026, 11, 7, 23, 59, 59),
            assignee_user_id=self.carol.id,
        )
        self.db.add(task)
        self.db.commit()

        with patch("app.services.jobs.task_reminders.utc_now", return_value=NOW), \
                patch(SERVICE_NOW, return_value=NOW):
            result = scan_task_due_reminders(self.db)

        self.assertEqual(result["escalated"], 1)
        escalation = (
            self.db.query(LegalNotificationEvent)
            .filter(
                LegalNotificationEvent.reference_id == task.id,
                LegalNotificationEvent.template_key == "task_owner_escalation_overdue_3d",
                LegalNotificationEvent.channel == "site",
            )
            .one()
        )
        self.assertEqual(escalation.user_id, self.alice.id)
        self.assertIsNone(escalation.on_behalf_of_user_id)

    def test_leave_status_labels(self):
        today = notification_service.local_today()
        leave = self._leave(self.alice, delegate=self.carol)
        self.assertEqual(notification_service.leave_status(leave), "active")

        leave.leave_start = today + timedelta(days=5)
        self.db.commit()
        self.assertEqual(notification_service.leave_status(leave), "scheduled")

        leave.leave_start = None
        leave.leave_end = today - timedelta(days=1)
        self.db.commit()
        self.assertEqual(notification_service.leave_status(leave), "expired")

        leave.delegate_user_id = None
        self.db.commit()
        self.assertEqual(notification_service.leave_status(leave), "none")


if __name__ == "__main__":
    unittest.main()
