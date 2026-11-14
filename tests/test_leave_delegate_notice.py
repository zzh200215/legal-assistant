"""请假代理通知：生效通知代理、交还通知代理、换人、幂等、不转给代理的代理。"""

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
from app.models.legal_notifications import LegalNotificationEvent
from app.models.org import Organization
from app.models.user import User
from app.services.jobs.delegate_reminders import scan_delegate_expiring
from app.services.notification.notification_service import (
    LEAVE_ENDED_TEMPLATE,
    LEAVE_STARTED_TEMPLATE,
    notification_service,
)

NOW = datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)  # 上海 11-10 18:00
SERVICE_NOW = "app.services.notification.notification_service.utc_now"


class LeaveDelegateNoticeTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        self.db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        self.org = Organization(name="请假通知律所", code="leave_notice")
        self.other_org = Organization(name="其它律所", code="leave_notice_other")
        self.db.add_all([self.org, self.other_org])
        self.db.flush()
        self.alice = self._user("notice-alice", "Alice", self.org.id)
        self.bob = self._user("notice-bob", "Bob", self.org.id)
        self.carol = self._user("notice-carol", "Carol", self.org.id)
        self.outsider = self._user("notice-outsider", "Outsider", self.other_org.id)
        self.disabled = self._user("notice-disabled", "Disabled", self.org.id,
                                   status="disabled")
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

    def _leave(self, user=None, *, delegate, start=None, end=None):
        user = user or self.alice
        with patch(SERVICE_NOW, return_value=NOW):
            return notification_service.set_leave(
                db=self.db,
                user_id=user.id,
                organization_id=user.organization_id,
                delegate_user_id=delegate.id if delegate else None,
                leave_start=start,
                leave_end=end,
            )

    def _events(self, user, *, template=None):
        query = self.db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.user_id == user.id)
        if template:
            query = query.filter(LegalNotificationEvent.template_key == template)
        return query.order_by(LegalNotificationEvent.id.asc()).all()

    def test_active_leave_notifies_delegate_immediately(self):
        leave = self._leave(delegate=self.bob)

        events = self._events(self.bob, template=LEAVE_STARTED_TEMPLATE)
        self.assertEqual({event.channel for event in events}, {"site", "email"})
        site = [event for event in events if event.channel == "site"][0]
        self.assertEqual(site.reference_type, "notification_leave")
        self.assertEqual(site.reference_id, leave.id)
        self.assertIsNone(site.on_behalf_of_user_id)
        self.assertEqual(site.title, "请假代收开始：代 Alice 接收通知")
        self.assertIn("请假中", site.body)
        self.assertIn("代 Alice 接收", site.body)

    def test_scheduled_leave_notifies_when_it_starts(self):
        self._leave(delegate=self.bob, start=date(2026, 11, 11))

        self.assertEqual(self._events(self.bob, template=LEAVE_STARTED_TEMPLATE), [])

        tomorrow = datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc)
        result = scan_delegate_expiring(self.db, now=tomorrow)

        self.assertEqual(result["leave_started"], 1)
        events = self._events(self.bob, template=LEAVE_STARTED_TEMPLATE)
        self.assertEqual({event.channel for event in events}, {"site", "email"})
        self.assertIn("2026-11-11", events[0].body)

    def test_repeated_save_does_not_repeat_notice(self):
        self._leave(delegate=self.bob)
        second = self._leave(delegate=self.bob)

        self.assertEqual(second.leave_period, 1)
        self.assertEqual(len(self._events(self.bob, template=LEAVE_STARTED_TEMPLATE)), 2)

    def test_ending_leave_notifies_delegate_once(self):
        self._leave(delegate=self.bob)

        with patch(SERVICE_NOW, return_value=NOW):
            self.assertTrue(notification_service.clear_leave(
                db=self.db, user_id=self.alice.id, organization_id=self.org.id,
            ))
            notification_service.clear_leave(
                db=self.db, user_id=self.alice.id, organization_id=self.org.id,
            )

        ended = self._events(self.bob, template=LEAVE_ENDED_TEMPLATE)
        self.assertEqual({event.channel for event in ended}, {"site", "email"})
        self.assertEqual(ended[0].title, "请假代收结束：已交还给 Alice")
        self.assertIn("不再转给你", ended[0].body)
        self.assertIn("本轮没有需要转发的通知", ended[0].body)

    def test_handback_reports_forwarded_count(self):
        """交还时告诉代理这轮代收了多少，站内与邮件同口径。"""
        self._leave(delegate=self.bob)
        for index in range(3):
            notification_service.create_multi_channel_notification(
                db=self.db,
                organization_id=self.org.id,
                user_id=self.alice.id,
                event_type="deadline",
                title=f"代收事件 {index}",
                channels=["site"],
                # 幂等键不含标题：用不同 reference 造出三条独立事件
                reference_type="task",
                reference_id=index + 1,
            )

        notification_service.clear_leave(
            db=self.db, user_id=self.alice.id, organization_id=self.org.id,
        )

        ended = self._events(self.bob, template=LEAVE_ENDED_TEMPLATE)
        self.assertEqual(len(ended), 2)  # site + email
        self.assertTrue(all("本轮你共代收 3 条通知" in event.body for event in ended))

    def test_handback_count_ignores_previous_period(self):
        """换代理时，旧代理只收到自己那轮的条数，新代理从 0 开始。"""
        self._leave(delegate=self.bob)
        for index in range(2):
            notification_service.create_multi_channel_notification(
                db=self.db,
                organization_id=self.org.id,
                user_id=self.alice.id,
                event_type="deadline",
                title=f"Bob 代收 {index}",
                channels=["site"],
                reference_type="task",
                reference_id=index + 1,
            )

        self._leave(delegate=self.carol)

        bob_ended = self._events(self.bob, template=LEAVE_ENDED_TEMPLATE)
        self.assertTrue(all("本轮你共代收 2 条通知" in event.body for event in bob_ended))
        carol_started = self._events(self.carol, template=LEAVE_STARTED_TEMPLATE)
        self.assertTrue(all("代 Alice 接收" in event.body for event in carol_started))

    def test_expiry_cleanup_sends_handback(self):
        self._leave(delegate=self.bob, end=date(2026, 11, 10))
        later = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)

        result = scan_delegate_expiring(self.db, now=later)

        self.assertEqual(result["leave_ended"], 1)
        self.assertEqual(result["leave_cleared"], 1)
        self.assertEqual(len(self._events(self.bob, template=LEAVE_ENDED_TEMPLATE)), 2)

    def test_cancelling_scheduled_leave_sends_no_handback(self):
        self._leave(delegate=self.bob, start=date(2026, 12, 1))

        with patch(SERVICE_NOW, return_value=NOW):
            notification_service.clear_leave(
                db=self.db, user_id=self.alice.id, organization_id=self.org.id,
            )

        self.assertEqual(self._events(self.bob, template=LEAVE_ENDED_TEMPLATE), [])

    def test_disabled_delegate_gets_no_notice(self):
        leave = self._leave(delegate=self.disabled)

        self.assertEqual(self._events(self.disabled), [])
        self.assertEqual(leave.delegate_user_id, self.disabled.id)

    def test_cross_org_delegate_gets_no_notice(self):
        self._leave(delegate=self.outsider)

        self.assertEqual(self._events(self.outsider), [])

    def test_delegate_change_hands_off_then_notifies_new_delegate(self):
        self._leave(delegate=self.bob)
        leave = self._leave(delegate=self.carol)

        self.assertEqual(leave.leave_period, 2)
        self.assertEqual(len(self._events(self.bob, template=LEAVE_STARTED_TEMPLATE)), 2)
        self.assertEqual(len(self._events(self.bob, template=LEAVE_ENDED_TEMPLATE)), 2)
        self.assertEqual(len(self._events(self.carol, template=LEAVE_STARTED_TEMPLATE)), 2)
        self.assertEqual(self._events(self.carol, template=LEAVE_ENDED_TEMPLATE), [])

    def test_new_period_after_end_notifies_again(self):
        self._leave(delegate=self.bob)
        with patch(SERVICE_NOW, return_value=NOW):
            notification_service.clear_leave(
                db=self.db, user_id=self.alice.id, organization_id=self.org.id,
            )
        leave = self._leave(delegate=self.bob)

        self.assertEqual(leave.leave_period, 2)
        started = self._events(self.bob, template=LEAVE_STARTED_TEMPLATE)
        self.assertEqual(len([e for e in started if e.channel == "site"]), 2)

    def test_notice_is_not_forwarded_to_delegate_own_delegate(self):
        # Bob 自己也设置了请假代理 Carol；Alice 请 Bob 代收时，通知仍直达 Bob。
        self._leave(self.bob, delegate=self.carol)
        self._leave(self.alice, delegate=self.bob)

        titled = (
            self.db.query(LegalNotificationEvent)
            .filter(LegalNotificationEvent.title.like("%代 Alice 接收%"))
            .all()
        )
        self.assertEqual({event.user_id for event in titled}, {self.bob.id})
        self.assertEqual({event.on_behalf_of_user_id for event in titled}, {None})

    def test_scan_is_idempotent_for_started_notice(self):
        self._leave(delegate=self.bob, start=date(2026, 11, 11))
        tomorrow = datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc)

        first = scan_delegate_expiring(self.db, now=tomorrow)
        second = scan_delegate_expiring(self.db, now=tomorrow)

        self.assertEqual(first["leave_started"], 1)
        self.assertEqual(second["leave_started"], 0)
        self.assertEqual(
            len([e for e in self._events(self.bob, template=LEAVE_STARTED_TEMPLATE)
                 if e.channel == "site"]),
            1,
        )


if __name__ == "__main__":
    unittest.main()
