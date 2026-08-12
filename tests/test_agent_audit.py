"""结构化审计：事件持久化、敏感字段脱敏、按 run 查询。"""

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.agent import AgentAuditEvent
from app.models.user import User
from app.services.agent_audit import agent_audit_service


class AgentAuditTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:", future=True,
            connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        self.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        self.db = self.SessionLocal()
        self.user = User(username="u", email="u@e.com", hashed_password="h")
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

    def tearDown(self):
        self.db.close()

    def test_record_serializes_and_redacts(self):
        event = agent_audit_service.record(
            db=self.db, event_type="permission_decision", run_id=1, step=2,
            trace_id="tr", user_id=self.user.id, tool_name="sql_query_tool",
            decision={"allowed": False, "reason": "denied"},
            summary={"input": {"sql": "SELECT secret", "token": "abc"}, "ok": True},
            error_category="permission_denied", status="denied", duration_ms=3,
        )
        self.assertEqual(event.event_type, "permission_decision")
        self.assertEqual(event.run_id, 1)
        self.assertEqual(event.trace_id, "tr")
        # token 脱敏、原始 SQL 摘要不落库
        self.assertNotIn("abc", event.summary_json)
        self.assertIn("****", event.summary_json)

    def test_list_for_run_ordered(self):
        agent_audit_service.record(db=self.db, event_type="plan_created", run_id=5, user_id=self.user.id)
        agent_audit_service.record(db=self.db, event_type="tool_executed", run_id=5, user_id=self.user.id)
        agent_audit_service.record(db=self.db, event_type="tool_executed", run_id=6, user_id=self.user.id)
        events = agent_audit_service.list_for_run(self.db, 5)
        self.assertEqual([e.event_type for e in events], ["plan_created", "tool_executed"])

    def test_empty_summary_ok(self):
        event = agent_audit_service.record(db=self.db, event_type="run_state_changed", run_id=1)
        self.assertIsNone(event.summary_json)
        self.assertIsNotNone(event.created_at)


if __name__ == "__main__":
    unittest.main()
