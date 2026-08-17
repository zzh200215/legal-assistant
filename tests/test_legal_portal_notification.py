"""US-001 — 门户链接到期/即将到期 → 自动通知律师（扫描任务幂等通知）"""
import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from unittest.mock import patch

import app.models  # noqa: F401
from app.core.auth import hash_password
from app.core.database import Base
from app.core.time import utc_now
from app.models.legal import LegalCase
from app.models.legal_portal import LegalPortalLink
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


class PortalExpiryNotificationTests(unittest.TestCase):
    """扫描任务：过期/即将到期时各创建一次站内通知（幂等）"""

    def setUp(self):
        self.engine = _make_engine()
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.SessionLocal = Session
        Base.metadata.create_all(bind=self.engine)
        self.db = Session()

        self.org = Organization(name="PortalNotifyOrg", code="PNTF")
        self.db.add(self.org)
        self.db.flush()

        self.lawyer = User(
            username="portal_lawyer",
            email="lawyer@t.com",
            hashed_password=hash_password("pw"),
            role="user",
            status=UserStatus.active.value,
            organization_id=self.org.id,
        )
        self.db.add(self.lawyer)
        self.db.flush()

        self.case = LegalCase(
            title="张先生劳动争议案",
            case_type="other",
            organization_id=self.org.id,
            user_id=self.lawyer.id,
        )
        self.db.add(self.case)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _make_link(self, *, expires_at: datetime | None, status: str = "active",
                   is_permanent: int = 0) -> LegalPortalLink:
        link = LegalPortalLink(
            organization_id=self.org.id,
            case_id=self.case.id,
            token_hash="h" * 64,
            token_prefix="abcd1234",
            status=status,
            is_permanent=is_permanent,
            expires_at=expires_at,
            require_email_verification=1,
            created_by=self.lawyer.id,
        )
        self.db.add(link)
        self.db.commit()
        self.db.refresh(link)
        return link

    def _events_for(self, link: LegalPortalLink) -> list[LegalNotificationEvent]:
        return self.db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.reference_type == "portal_link",
            LegalNotificationEvent.reference_id == link.id,
        ).all()

    def _run_scan(self):
        from app.tasks import scan_expired_portal_links_task
        with patch("app.tasks.legal_tasks.SessionLocal", self.SessionLocal):
            return scan_expired_portal_links_task()

    def test_expired_link_creates_delivered_notification(self):
        link = self._make_link(expires_at=utc_now() - timedelta(hours=1))
        self._run_scan()
        self.db.refresh(link)
        self.assertEqual(link.status, "expired")
        events = self._events_for(link)
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev.event_type, "portal")
        self.assertEqual(ev.channel, "site")
        self.assertEqual(ev.status, "delivered")
        self.assertEqual(ev.user_id, self.lawyer.id)
        self.assertEqual(ev.organization_id, self.org.id)
        self.assertEqual(ev.case_id, self.case.id)
        self.assertEqual(ev.body, f"portal_link:{link.id}:expired")
        self.assertIn("张先生劳动争议案", ev.title)

    def test_expired_notification_is_idempotent(self):
        link = self._make_link(expires_at=utc_now() - timedelta(hours=1))
        self._run_scan()
        self._run_scan()
        self.assertEqual(len(self._events_for(link)), 1)

    def test_about_to_expire_link_creates_expiring_notification(self):
        link = self._make_link(expires_at=utc_now() + timedelta(days=2))
        self._run_scan()
        self.db.refresh(link)
        self.assertEqual(link.status, "active")
        events = self._events_for(link)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].body, f"portal_link:{link.id}:expiring_soon")
        self.assertIn("即将到期", events[0].title)

    def test_far_future_link_creates_no_notification(self):
        link = self._make_link(expires_at=utc_now() + timedelta(days=10))
        self._run_scan()
        self.assertEqual(self._events_for(link), [])
        self.db.refresh(link)
        self.assertEqual(link.status, "active")

    def test_revoked_link_not_notified(self):
        link = self._make_link(expires_at=utc_now() - timedelta(hours=1), status="revoked")
        self._run_scan()
        self.assertEqual(self._events_for(link), [])

    def test_notification_title_falls_back_to_case_id_when_case_missing(self):
        link = LegalPortalLink(
            organization_id=self.org.id,
            case_id=999999,
            token_hash="g" * 64,
            token_prefix="fallback1",
            status="active",
            is_permanent=0,
            expires_at=utc_now() - timedelta(hours=1),
            require_email_verification=1,
            created_by=self.lawyer.id,
        )
        self.db.add(link)
        self.db.commit()
        self._run_scan()
        events = self._events_for(link)
        self.assertEqual(len(events), 1)
        self.assertIn(f"案件#{link.case_id}", events[0].title)


if __name__ == "__main__":
    unittest.main()
