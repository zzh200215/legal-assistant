"""代收来源：请假代收与按事件类型代收在事件上留下不同来源；本人接收为空。"""

import unittest
from datetime import timedelta

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
from app.services.notification.notification_service import notification_service


class DelegationSourceTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        self.db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        self.org = Organization(name="代收来源律所", code="delegation_source_firm")
        self.other_org = Organization(name="其它律所", code="delegation_source_other")
        self.db.add_all([self.org, self.other_org])
        self.db.flush()
        self.alice = self._user("source-alice", "Alice", self.org.id)
        self.bob = self._user("source-bob", "Bob", self.org.id)
        self.carol = self._user("source-carol", "Carol", self.org.id)
        self.outsider = self._user("source-outsider", "Outsider", self.other_org.id)
        self.db.commit()
        self.db.add(OutboundEmailPolicy(organization_id=self.org.id, enabled=True))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _user(self, username, full_name, org_id):
        user = User(
            username=username,
            email=f"{username}@example.com",
            hashed_password=hash_password("pw"),
            full_name=full_name,
            organization_id=org_id,
            status="active",
        )
        self.db.add(user)
        self.db.flush()
        return user

    def _pref(self, user, *, delegate=None, event_type="deadline"):
        row = LegalNotificationPreference(
            user_id=user.id,
            organization_id=user.organization_id,
            event_type=event_type,
            channels_json='["site"]',
            delegate_user_id=delegate.id if delegate else None,
            timezone="Asia/Shanghai",
        )
        self.db.add(row)
        self.db.commit()
        return row

    def _leave(self, user, delegate, *, start=None, end=None):
        row = LegalNotificationLeave(
            user_id=user.id,
            organization_id=user.organization_id,
            delegate_user_id=delegate.id if delegate else None,
            leave_start=start,
            leave_end=end,
        )
        self.db.add(row)
        self.db.commit()
        return row

    def _create(self, user, *, title="来源测试通知", event_type="deadline"):
        return notification_service.create_multi_channel_notification(
            db=self.db,
            organization_id=user.organization_id,
            user_id=user.id,
            event_type=event_type,
            title=title,
            body="通知正文",
            channels=["site"],
        )

    def _events(self, user_id):
        return self.db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.user_id == user_id).all()

    def test_leave_delegation_records_leave_source(self):
        self._leave(self.alice, self.bob)
        events = self._create(self.alice)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].user_id, self.bob.id)
        self.assertEqual(events[0].on_behalf_of_user_id, self.alice.id)
        self.assertEqual(events[0].on_behalf_of_source, "leave")

    def test_event_delegation_records_event_source(self):
        self._pref(self.alice, delegate=self.bob)
        events = self._create(self.alice)

        self.assertEqual(events[0].user_id, self.bob.id)
        self.assertEqual(events[0].on_behalf_of_user_id, self.alice.id)
        self.assertEqual(events[0].on_behalf_of_source, "event")

    def test_own_notification_has_no_source(self):
        events = self._create(self.alice)

        self.assertEqual(events[0].user_id, self.alice.id)
        self.assertIsNone(events[0].on_behalf_of_user_id)
        self.assertIsNone(events[0].on_behalf_of_source)

    def test_leave_takes_precedence_over_event_delegate(self):
        self._pref(self.alice, delegate=self.carol)
        self._leave(self.alice, self.bob)
        events = self._create(self.alice)

        self.assertEqual(events[0].user_id, self.bob.id)
        self.assertEqual(events[0].on_behalf_of_source, "leave")

    def test_ended_leave_falls_back_to_event_source(self):
        self._pref(self.alice, delegate=self.carol)
        today = notification_service.local_today("Asia/Shanghai")
        self._leave(
            self.alice, self.bob,
            start=today - timedelta(days=30),
            end=today - timedelta(days=1),
        )
        events = self._create(self.alice)

        self.assertEqual(events[0].user_id, self.carol.id)
        self.assertEqual(events[0].on_behalf_of_source, "event")

    def test_leave_with_invalid_delegate_falls_back_to_own(self):
        self._leave(self.alice, self.outsider)
        events = self._create(self.alice)

        self.assertEqual(events[0].user_id, self.alice.id)
        self.assertIsNone(events[0].on_behalf_of_source)

    def test_source_ignored_without_on_behalf(self):
        event = notification_service.create_notification(
            db=self.db,
            organization_id=self.org.id,
            user_id=self.alice.id,
            event_type="deadline",
            title="无代理通知",
            channel="site",
            on_behalf_of_source="leave",
        )
        self.assertIsNone(event.on_behalf_of_user_id)
        self.assertIsNone(event.on_behalf_of_source)

    def test_unsupported_source_rejected(self):
        with self.assertRaises(ValueError):
            notification_service.create_notification(
                db=self.db,
                organization_id=self.org.id,
                user_id=self.bob.id,
                event_type="deadline",
                title="非法来源",
                channel="site",
                on_behalf_of_user_id=self.alice.id,
                on_behalf_of_source="escalation",
            )

    def test_serialized_event_exposes_source(self):
        self._leave(self.alice, self.bob)
        event = self._create(self.alice)[0]
        payload = notification_service.serialize_event(event)

        self.assertEqual(payload["on_behalf_of_source"], "leave")
        self.assertEqual(payload["on_behalf_of_user_id"], self.alice.id)


if __name__ == "__main__":
    unittest.main()
