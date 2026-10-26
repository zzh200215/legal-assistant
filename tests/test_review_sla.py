"""审核 SLA 站内提醒：到期待审记录 → 审核人一次「即将到期 / 已逾期」通知。"""

import unittest
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import hash_password
from app.core.database import Base
from app.core.time import utc_now
from app.models.legal import LegalConsultation
from app.models.legal_notifications import LegalNotificationEvent
from app.models.org import Organization
from app.models.user import User
from app.services.legal.review_sla import scan_review_sla


class ScanReviewSlaTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:", future=True,
            connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        self.db = self.Session()
        self.org = Organization(name="SLA 律所", code="sla_firm")
        self.reviewer = User(
            username="sla-reviewer", email="sla-reviewer@example.com",
            hashed_password=hash_password("pw"), organization_id=None,
        )
        self.db.add_all([self.org, self.reviewer])
        self.db.commit()
        self.db.refresh(self.org)
        self.db.refresh(self.reviewer)
        self.reviewer.organization_id = self.org.id
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _row(self, *, hours, reviewer_id="default", status="needs_lawyer_review", title="待审咨询"):
        row = LegalConsultation(
            user_id=self.reviewer.id, question=title, category="labor",
            known_facts_json="[]", missing_facts_json="[]", references_json="[]",
            advice="a", risk_level="low", status=status,
            reviewer_id=self.reviewer.id if reviewer_id == "default" else reviewer_id,
            review_due_at=utc_now() + timedelta(hours=hours),
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def _events(self):
        return self.db.query(LegalNotificationEvent).all()

    def test_creates_due_soon_and_overdue_once(self):
        soon = self._row(hours=6, title="六小时后到期")
        late = self._row(hours=-50, title="已超时两天")
        self._row(hours=72, title="三天后到期")
        self._row(hours=6, reviewer_id=None, title="未分配")

        result = scan_review_sla(self.db)
        self.assertEqual(result, {"created": 2, "due_soon": 1, "overdue": 1})
        events = self._events()
        self.assertEqual(len(events), 2)
        by_ref = {event.reference_id: event for event in events}
        self.assertIn("审核即将到期", by_ref[soon.id].title)
        self.assertIn("剩 6 小时", by_ref[soon.id].title)
        self.assertEqual(by_ref[soon.id].template_key, "review_sla_due_soon")
        self.assertIn("审核已逾期", by_ref[late.id].title)
        self.assertIn("超时 2 天", by_ref[late.id].title)
        self.assertEqual(by_ref[late.id].template_key, "review_sla_overdue")
        self.assertEqual(by_ref[late.id].organization_id, self.org.id)

        # 幂等：再扫一次不重复
        self.assertEqual(scan_review_sla(self.db), {"created": 0, "due_soon": 0, "overdue": 0})

    def test_email_channel_created_when_org_has_outbound_policy(self):
        """组织配置外发邮件策略时，SLA 提醒同时落站内 + 邮件两个事件。"""
        from app.models.email import OutboundEmailPolicy

        self.db.add(OutboundEmailPolicy(organization_id=self.org.id, enabled=True))
        self.db.commit()
        row = self._row(hours=6, title="邮件提醒咨询")

        result = scan_review_sla(self.db)
        self.assertEqual(result["due_soon"], 1)
        events = self.db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.reference_id == row.id,
        ).all()
        channels = sorted((event.channel, event.status) for event in events)
        self.assertEqual(channels, [("email", "pending"), ("site", "delivered")])
        self.assertTrue(all(event.template_key == "review_sla_due_soon" for event in events))

        # 幂等：同模板键不重复建事件（含邮件渠道）
        self.assertEqual(scan_review_sla(self.db)["created"], 0)

    def test_stage_change_from_due_soon_to_overdue_notifies_again(self):
        row = self._row(hours=6, title="先即将到期后逾期")
        scan_review_sla(self.db)
        later = utc_now() + timedelta(hours=8)
        result = scan_review_sla(self.db, now=later)
        self.assertEqual(result["overdue"], 1)
        stages = [
            event.template_key for event in self._events()
            if event.reference_id == row.id
        ]
        self.assertEqual(sorted(stages), ["review_sla_due_soon", "review_sla_overdue"])
