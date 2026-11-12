"""请假回执：请假人收到「已生效 / 已恢复」，按轮次去重，不被自己的代理转走。"""

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
    LEAVE_OWNER_RESUMED_TEMPLATE,
    LEAVE_OWNER_STARTED_TEMPLATE,
    notification_service,
)

NOW = datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)  # 上海 11-10 18:00
SERVICE_NOW = "app.services.notification.notification_service.utc_now"


class LeaveOwnerReceiptTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        self.db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        self.org = Organization(name="回执测试律所", code="owner_receipt")
        self.db.add(self.org)
        self.db.flush()
        self.alice = self._user("receipt-alice", "Alice")
        self.bob = self._user("receipt-bob", "Bob")
        self.carol = self._user("receipt-carol", "Carol")
        self.disabled = self._user("receipt-disabled", "Disabled", status="disabled")
        self.db.commit()
        self.db.add(OutboundEmailPolicy(organization_id=self.org.id, enabled=True))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _user(self, username, full_name, status="active"):
        user = User(
            username=username,
            email=f"{username}@example.com",
            hashed_password=hash_password("pw"),
            full_name=full_name,
            organization_id=self.org.id,
            status=status,
        )
        self.db.add(user)
        self.db.flush()
        return user

    def _leave(self, *, delegate, start=None, end=None):
        with patch(SERVICE_NOW, return_value=NOW):
            return notification_service.set_leave(
                db=self.db,
                user_id=self.alice.id,
                organization_id=self.org.id,
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

    def _forwarded(self, title, *, channels=None, ref=None):
        """请假期间给 Alice 发一条通知：会被转发给代理，计入代收条数。

        幂等键不含标题，所以多条通知要用不同 reference_id 区分，否则会被去重成一条。
        """
        notification_service.create_multi_channel_notification(
            db=self.db,
            organization_id=self.org.id,
            user_id=self.alice.id,
            event_type="deadline",
            title=title,
            body="正文",
            channels=channels or ["site"],
            reference_type="task" if ref is not None else None,
            reference_id=ref,
        )

    def _clear(self):
        with patch(SERVICE_NOW, return_value=NOW):
            return notification_service.clear_leave(
                db=self.db, user_id=self.alice.id, organization_id=self.org.id,
            )

    def _resumed_site(self):
        return [event for event in
                self._events(self.alice, template=LEAVE_OWNER_RESUMED_TEMPLATE)
                if event.channel == "site"]

    def test_active_leave_sends_receipt_to_owner(self):
        leave = self._leave(delegate=self.bob)

        events = self._events(self.alice, template=LEAVE_OWNER_STARTED_TEMPLATE)
        self.assertEqual({event.channel for event in events}, {"site", "email"})
        site = [event for event in events if event.channel == "site"][0]
        self.assertEqual(site.title, "请假已生效：通知转给 Bob")
        self.assertIn("期间所有事件类型的通知都会转给 Bob", site.body)
        self.assertIsNone(site.on_behalf_of_user_id)
        self.assertEqual(site.reference_type, "notification_leave")
        self.assertEqual(site.reference_id, leave.id)

    def test_scheduled_leave_receipt_arrives_when_started(self):
        self._leave(delegate=self.bob, start=date(2026, 11, 11))

        self.assertEqual(self._events(self.alice, template=LEAVE_OWNER_STARTED_TEMPLATE), [])

        tomorrow = datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc)
        scan_delegate_expiring(self.db, now=tomorrow)

        events = self._events(self.alice, template=LEAVE_OWNER_STARTED_TEMPLATE)
        self.assertEqual({event.channel for event in events}, {"site", "email"})
        self.assertIn("2026-11-11 起", events[0].body)

    def test_repeated_save_does_not_repeat_receipt(self):
        self._leave(delegate=self.bob)
        self._leave(delegate=self.bob)

        self.assertEqual(
            len(self._events(self.alice, template=LEAVE_OWNER_STARTED_TEMPLATE)), 2,
        )

    def test_ending_leave_sends_resumed_receipt_once(self):
        self._leave(delegate=self.bob)

        with patch(SERVICE_NOW, return_value=NOW):
            notification_service.clear_leave(
                db=self.db, user_id=self.alice.id, organization_id=self.org.id,
            )
            notification_service.clear_leave(
                db=self.db, user_id=self.alice.id, organization_id=self.org.id,
            )

        events = self._events(self.alice, template=LEAVE_OWNER_RESUMED_TEMPLATE)
        self.assertEqual({event.channel for event in events}, {"site", "email"})
        site = [event for event in events if event.channel == "site"][0]
        self.assertEqual(site.title, "通知已恢复：请假结束")
        self.assertIn("不再代收", site.body)

    def test_change_delegate_receipts_new_agent_without_resumed(self):
        self._leave(delegate=self.bob)
        self._leave(delegate=self.carol)

        started = [event for event in
                   self._events(self.alice, template=LEAVE_OWNER_STARTED_TEMPLATE)
                   if event.channel == "site"]
        self.assertEqual(len(started), 2)
        self.assertIn("Carol", started[-1].body)
        self.assertEqual(self._events(self.alice, template=LEAVE_OWNER_RESUMED_TEMPLATE), [])

    def test_cancelled_before_start_sends_no_resumed(self):
        self._leave(delegate=self.bob, start=date(2026, 12, 1))

        with patch(SERVICE_NOW, return_value=NOW):
            notification_service.clear_leave(
                db=self.db, user_id=self.alice.id, organization_id=self.org.id,
            )

        self.assertEqual(self._events(self.alice, template=LEAVE_OWNER_RESUMED_TEMPLATE), [])

    def test_expiry_cleanup_sends_resumed_receipt(self):
        self._leave(delegate=self.bob, end=date(2026, 11, 10))
        later = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)

        scan_delegate_expiring(self.db, now=later)

        events = self._events(self.alice, template=LEAVE_OWNER_RESUMED_TEMPLATE)
        self.assertEqual(len(events), 2)
        self.assertIn("Bob 不再代收", events[0].body)

    def test_invalid_delegate_sends_no_receipt(self):
        self._leave(delegate=self.disabled)

        self.assertEqual(self._events(self.alice, template=LEAVE_OWNER_STARTED_TEMPLATE), [])

    def test_receipt_is_not_forwarded_to_owner_own_delegate(self):
        # Alice 的请假把通知转给 Bob；「已生效」回执不能也被转给 Bob。
        self._leave(delegate=self.bob)

        self.assertEqual(self._events(self.bob, template=LEAVE_OWNER_STARTED_TEMPLATE), [])
        receipt = self._events(self.alice, template=LEAVE_OWNER_STARTED_TEMPLATE)
        self.assertEqual(len(receipt), 2)

    def test_no_delegate_no_receipt(self):
        self._leave(delegate=None)

        self.assertEqual(self._events(self.alice, template=LEAVE_OWNER_STARTED_TEMPLATE), [])

    def test_resumed_receipt_counts_forwarded_notifications(self):
        self._leave(delegate=self.bob)
        for index in range(3):
            self._forwarded(f"代收通知 {index}", ref=index)

        self._clear()

        site = self._resumed_site()
        self.assertEqual(len(site), 1)
        self.assertIn("期间共 3 条通知由 Bob 代收", site[0].body)

    def test_resumed_receipt_reports_zero_forwarded(self):
        self._leave(delegate=self.bob)

        self._clear()

        self.assertIn("期间没有产生需要转发的通知", self._resumed_site()[0].body)

    def test_resumed_count_is_per_notification_not_per_channel(self):
        self._leave(delegate=self.bob)
        self._forwarded("站内加邮件", channels=["site", "email"])

        self._clear()

        self.assertIn("期间共 1 条通知由 Bob 代收", self._resumed_site()[0].body)

    def test_resumed_count_only_covers_current_period(self):
        self._leave(delegate=self.bob)
        self._forwarded("第一轮通知")
        self._clear()

        self._leave(delegate=self.bob)
        self._forwarded("第二轮通知 1", ref=101)
        self._forwarded("第二轮通知 2", ref=102)
        self._clear()

        site = self._resumed_site()
        self.assertEqual(len(site), 2)
        self.assertIn("期间共 1 条通知", site[0].body)
        self.assertIn("期间共 2 条通知", site[1].body)


if __name__ == "__main__":
    unittest.main()
