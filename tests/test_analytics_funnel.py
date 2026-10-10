"""产品漏斗埋点测试：事件落库、未知事件跳过、SAVEPOINT 隔离（埋点失败不伤业务）、漏斗汇总聚合。"""
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.analytics_funnel import AnalyticsFunnelEvent
from app.models.legal_portal import LegalPortalAccessLog
from app.services.observability.funnel_service import funnel_summary, record_event


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


class AnalyticsFunnelTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_record_event_persists_valid_event(self):
        record_event(self.session, "auth_register", user_id=7, organization_id=3)
        self.session.commit()
        row = self.session.query(AnalyticsFunnelEvent).one()
        self.assertEqual(row.event, "auth_register")
        self.assertEqual(row.user_id, 7)
        self.assertEqual(row.organization_id, 3)

    def test_record_event_rejects_unknown_event(self):
        record_event(self.session, "not_a_funnel_event", user_id=1)
        self.session.commit()
        self.assertEqual(self.session.query(AnalyticsFunnelEvent).count(), 0)

    def test_record_event_props_serialized(self):
        record_event(self.session, "review_submit", user_id=2, props={"action": "approve", "target_type": "consultation"})
        self.session.commit()
        row = self.session.query(AnalyticsFunnelEvent).one()
        self.assertIn("approve", row.props_json)

    def test_record_event_failure_does_not_break_business_data(self):
        """SAVEPOINT 隔离：埋点 flush 失败只回滚埋点行，同事务的业务数据照常提交。"""
        from app.models.user import User

        self.session.add(User(username="u1", email="u1@x.com", hashed_password="x", role="user", status="active"))
        self.session.flush()
        # 模拟埋点写库失败：props 序列化阶段抛错，record_event 必须吞掉且不影响外层事务
        with patch("app.services.observability.funnel_service.json.dumps", side_effect=RuntimeError("boom")):
            record_event(self.session, "case_create", user_id=1, props={"k": "v"})
        self.session.commit()
        # 业务数据仍在
        self.assertEqual(self.session.query(User).count(), 1)
        # 坏埋点被吞，未产生半行
        self.assertEqual(self.session.query(AnalyticsFunnelEvent).count(), 0)

    def test_funnel_summary_aggregates_and_portal_visits(self):
        for uid in (1, 1, 2):
            record_event(self.session, "auth_register", user_id=uid, organization_id=9)
        record_event(self.session, "case_create", user_id=1, organization_id=9, case_id=5)
        record_event(self.session, "consult_submit", user_id=1, organization_id=9, case_id=5)
        record_event(self.session, "review_submit", user_id=2, organization_id=9, props={"action": "approve"})
        self.session.add(LegalPortalAccessLog(portal_link_id=1, organization_id=9, action="view", result="success"))
        self.session.commit()

        summary = funnel_summary(self.session, days=30)
        events = summary["events"]
        self.assertEqual(events["auth_register"]["total"], 3)
        self.assertEqual(events["auth_register"]["unique_users"], 2)
        self.assertEqual(events["case_create"]["total"], 1)
        self.assertEqual(events["consult_submit"]["total"], 1)
        self.assertEqual(events["review_submit"]["total"], 1)
        self.assertEqual(events["auth_login"]["total"], 0)
        self.assertEqual(sum(item["count"] for item in events["auth_register"]["by_day"]), 3)
        self.assertEqual(summary["portal_visits"]["total"], 1)

    def test_funnel_summary_empty_window(self):
        summary = funnel_summary(self.session, days=7)
        self.assertEqual(summary["days"], 7)
        for event in ("auth_register", "auth_login", "case_create", "consult_submit", "review_submit"):
            self.assertEqual(summary["events"][event]["total"], 0)
        self.assertEqual(summary["portal_visits"]["total"], 0)


if __name__ == "__main__":
    unittest.main()
