"""代理接收人：通知改发代理、原收件人入审计字段、失败回退、强提醒穿透。"""

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

NOW = datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)
SERVICE_NOW = "app.services.notification.notification_service.utc_now"


class NotificationDelegationTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        self.db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        self.org = Organization(name="代理测试律所", code="delegate_firm")
        self.other_org = Organization(name="其它律所", code="delegate_other")
        self.db.add_all([self.org, self.other_org])
        self.db.flush()
        self.alice = self._user("delegate-alice", "Alice", self.org.id)
        self.bob = self._user("delegate-bob", "Bob", self.org.id)
        self.carol = self._user("delegate-carol", "Carol", self.org.id)
        self.outsider = self._user("delegate-outsider", "Outsider", self.other_org.id)
        self.disabled = self._user("delegate-disabled", "Disabled", self.org.id, status="disabled")
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

    def _pref(self, user, *, delegate=None, channels='["site","email"]',
              mute_start=None, mute_end=None, timezone_name="Asia/Shanghai"):
        row = LegalNotificationPreference(
            user_id=user.id,
            organization_id=user.organization_id,
            event_type="deadline",
            channels_json=channels,
            delegate_user_id=delegate.id if delegate else None,
            mute_start=mute_start,
            mute_end=mute_end,
            timezone=timezone_name,
        )
        self.db.add(row)
        self.db.commit()
        return row

    def _create(self, user, *, title="代理测试通知", body="通知正文", event_type="deadline"):
        return notification_service.create_multi_channel_notification(
            db=self.db,
            organization_id=user.organization_id,
            user_id=user.id,
            event_type=event_type,
            title=title,
            body=body,
            channels=["site", "email"],
        )

    def _events(self, user):
        return (
            self.db.query(LegalNotificationEvent)
            .filter(LegalNotificationEvent.user_id == user.id)
            .all()
        )

    def test_delegate_receives_instead_of_original(self):
        self._pref(self.alice, delegate=self.bob)

        with patch(SERVICE_NOW, return_value=NOW):
            events = self._create(self.alice)
            again = self._create(self.alice)

        self.assertEqual({event.user_id for event in events}, {self.bob.id})
        self.assertEqual({event.on_behalf_of_user_id for event in events}, {self.alice.id})
        self.assertEqual(self._events(self.alice), [])
        self.assertEqual({event.id for event in again}, {event.id for event in events})
        self.assertTrue(all("代 Alice 接收" in event.body for event in events))

    def test_no_delegate_keeps_original(self):
        events = self._create(self.alice)

        self.assertEqual({event.user_id for event in events}, {self.alice.id})
        self.assertTrue(all(event.on_behalf_of_user_id is None for event in events))
        self.assertNotIn("代 ", events[0].body)

    def test_disabled_delegate_falls_back(self):
        self._pref(self.alice, delegate=self.disabled)

        events = self._create(self.alice)

        self.assertEqual({event.user_id for event in events}, {self.alice.id})
        self.assertTrue(all(event.on_behalf_of_user_id is None for event in events))

    def test_cross_org_delegate_falls_back(self):
        self._pref(self.alice, delegate=self.outsider)

        events = self._create(self.alice)

        self.assertEqual({event.user_id for event in events}, {self.alice.id})
        self.assertTrue(all(event.on_behalf_of_user_id is None for event in events))

    def test_delegate_to_self_falls_back(self):
        self._pref(self.alice, delegate=self.alice)

        events = self._create(self.alice)

        self.assertEqual({event.user_id for event in events}, {self.alice.id})
        self.assertTrue(all(event.on_behalf_of_user_id is None for event in events))

    def test_delegate_one_hop_only(self):
        self._pref(self.alice, delegate=self.bob)
        self._pref(self.bob, delegate=self.carol)

        events = self._create(self.alice)

        self.assertEqual({event.user_id for event in events}, {self.bob.id})
        self.assertEqual({event.on_behalf_of_user_id for event in events}, {self.alice.id})

    def test_delegate_channels_and_quiet_follow_delegate(self):
        self._pref(self.alice, delegate=self.bob)
        self._pref(self.bob, mute_start="22:00", mute_end="07:00")
        quiet_now = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)  # 上海 23:00

        with patch(SERVICE_NOW, return_value=quiet_now):
            events = self._create(self.alice)

        by_channel = {event.channel: event for event in events}
        self.assertEqual(by_channel["email"].scheduled_at.replace(tzinfo=timezone.utc),
                         datetime(2026, 11, 10, 23, 0, tzinfo=timezone.utc))
        self.assertIn("代 Alice 接收", by_channel["email"].body)
        self.assertIn("静默时段", by_channel["email"].body)

    def test_two_originals_same_delegate_are_not_deduped(self):
        self._pref(self.alice, delegate=self.bob)
        self._pref(self.carol, delegate=self.bob)

        events_alice = self._create(self.alice, title="同标题通知")
        events_carol = self._create(self.carol, title="同标题通知")

        self.assertEqual(len(events_alice), 2)
        self.assertEqual(len(events_carol), 2)
        originals = {
            event.on_behalf_of_user_id
            for event in self._events(self.bob)
        }
        self.assertEqual(originals, {self.alice.id, self.carol.id})

    def test_task_reminder_delegated_dedupes_across_scans(self):
        self._pref(self.alice, delegate=self.bob)
        task = Task(
            user_id=self.alice.id,
            organization_id=self.org.id,
            title="代理任务提醒",
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
            first = scan_task_due_reminders(self.db)
            second = scan_task_due_reminders(self.db)

        self.assertEqual(first["created"], 1)
        self.assertEqual(second["created"], 0)
        events = self._events(self.bob)
        self.assertEqual(len(events), 2)  # site + email，各一条
        self.assertTrue(all(event.on_behalf_of_user_id == self.alice.id for event in events))

    def test_task_escalation_not_delegated(self):
        self._pref(self.alice, delegate=self.bob)
        task = Task(
            user_id=self.alice.id,
            organization_id=self.org.id,
            title="升级不代理",
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
        self.assertFalse(any(event.user_id == self.bob.id for event in self._events(self.bob)))


if __name__ == "__main__":
    unittest.main()
