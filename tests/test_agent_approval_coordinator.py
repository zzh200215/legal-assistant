"""ApprovalCoordinator：生命周期（pending/approved/rejected/executed/expired/revoked）、
参数摘要、过期、撤销。"""

import unittest
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.core.time import utc_now
from app.models.user import User
from app.services.agent_approval_service import (
    ApprovalStateError,
    agent_approval_service,
    param_digest,
)


class ApprovalCoordinatorTests(unittest.TestCase):
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

    def _create(self, **kw):
        params = {
            "user_id": self.user.id, "db": self.db, "tool_name": "task_create_tool",
            "input_params": {"title": "t"}, "agent_type": "workflow_agent",
        }
        params.update(kw)
        return agent_approval_service.create_request(**params)

    def test_create_binds_digest_and_expiry(self):
        req = self._create(agent_run_id=1, step_id=2)
        self.assertEqual(req.status, "pending")
        self.assertEqual(req.param_digest, param_digest({"title": "t"}))
        self.assertEqual(req.step_id, 2)
        self.assertIsNotNone(req.expires_at)

    def test_decide_sets_actor(self):
        req = self._create()
        agent_approval_service.decide_request(db=self.db, approval_id=req.id, user_id=self.user.id, approved=True, decision_note="ok")
        req = agent_approval_service.get_request(db=self.db, approval_id=req.id, user_id=self.user.id)
        self.assertEqual(req.status, "approved")
        self.assertEqual(req.decided_by, self.user.id)

    def test_rejected_and_reject_after_decided(self):
        req = self._create()
        agent_approval_service.decide_request(db=self.db, approval_id=req.id, user_id=self.user.id, approved=False)
        with self.assertRaises(ValueError):
            agent_approval_service.decide_request(db=self.db, approval_id=req.id, user_id=self.user.id, approved=True)

    def test_expired_pending_not_executable(self):
        req = self._create()
        req.expires_at = utc_now() - timedelta(seconds=1)
        self.db.add(req)
        self.db.commit()
        # 决策/执行路径会惰性刷新：过期 pending 不可执行
        with self.assertRaises(ApprovalStateError):
            agent_approval_service.require_executable(db=self.db, approval_id=req.id, user_id=self.user.id)
        req = agent_approval_service.get_request(db=self.db, approval_id=req.id, user_id=self.user.id)
        self.assertEqual(req.status, "expired")
        # 已过期不可再批准
        with self.assertRaises(ValueError):
            agent_approval_service.decide_request(db=self.db, approval_id=req.id, user_id=self.user.id, approved=True)

    def test_revoke(self):
        req = self._create()
        req = agent_approval_service.revoke_request(db=self.db, approval_id=req.id, user_id=self.user.id, reason="manual")
        self.assertEqual(req.status, "revoked")
        with self.assertRaises(ApprovalStateError):
            agent_approval_service.require_executable(db=self.db, approval_id=req.id, user_id=self.user.id)

    def test_param_digest_stable_and_sensitive(self):
        self.assertEqual(param_digest({"a": 1, "b": 2}), param_digest({"b": 2, "a": 1}))
        self.assertNotEqual(param_digest({"a": 1}), param_digest({"a": 2}))

    def test_expire_stale(self):
        req = self._create()
        req.expires_at = utc_now() - timedelta(seconds=1)
        self.db.add(req)
        self.db.commit()
        self.assertEqual(agent_approval_service.expire_stale(self.db), 1)
        req = agent_approval_service.get_request(db=self.db, approval_id=req.id, user_id=self.user.id)
        self.assertEqual(req.status, "expired")


if __name__ == "__main__":
    unittest.main()
