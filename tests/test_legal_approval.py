"""Phase 9 Week 3 tests: 多级审批链（串行+并行+超时）"""
import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import create_access_token, hash_password
from app.core.database import Base, get_db
from app.main import app
from app.models.legal import LegalApprovalChain, LegalApprovalStep
from app.models.org import Organization, OrganizationMember
from app.models.user import User
from app.services.legal.legal_approval_service import LegalApprovalService
from fastapi.testclient import TestClient


class ApprovalServiceTests(unittest.TestCase):
    """单元测试：直接测试 LegalApprovalService 逻辑，不经过 HTTP 层。"""

    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        self.db = SessionLocal()
        self.svc = LegalApprovalService()

        # Users
        self.u1 = User(username="approver1", email="a1@x.com", hashed_password=hash_password("pw"), role="user")
        self.u2 = User(username="approver2", email="a2@x.com", hashed_password=hash_password("pw"), role="user")
        self.u3 = User(username="approver3", email="a3@x.com", hashed_password=hash_password("pw"), role="user")
        self.creator = User(username="creator", email="c@x.com", hashed_password=hash_password("pw"), role="user")
        self.db.add_all([self.u1, self.u2, self.u3, self.creator])
        self.db.commit()
        for u in [self.u1, self.u2, self.u3, self.creator]:
            self.db.refresh(u)

        self.org = Organization(name="律所W3", code="firm_w3")
        self.db.add(self.org)
        self.db.commit()
        self.db.refresh(self.org)

    def tearDown(self):
        self.db.close()

    def _create_serial(self, approvers=None, timeout=None):
        approvers = approvers or [
            {"user_id": self.u1.id, "role": "editor"},
            {"user_id": self.u2.id, "role": "reviewer"},
        ]
        return self.svc.create_chain(
            db=self.db, org_id=self.org.id,
            target_type="draft", target_id=1,
            chain_type="serial", approvers=approvers,
            timeout_hours=timeout, created_by=self.creator.id,
        )

    def _create_parallel(self, approvers=None):
        approvers = approvers or [
            {"user_id": self.u1.id, "role": "reviewer"},
            {"user_id": self.u2.id, "role": "reviewer"},
        ]
        return self.svc.create_chain(
            db=self.db, org_id=self.org.id,
            target_type="contract_review", target_id=2,
            chain_type="parallel", approvers=approvers,
            created_by=self.creator.id,
        )

    # ── Serial chain ──────────────────────────────────────────────────────────

    def test_serial_chain_first_step_pending(self):
        chain = self._create_serial()
        steps = self.svc.get_chain_steps(db=self.db, chain_id=chain.id)
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0].status, "pending")
        self.assertEqual(steps[1].status, "waiting")
        self.assertEqual(chain.status, "in_progress")

    def test_serial_chain_approve_first_activates_second(self):
        chain = self._create_serial()
        chain = self.svc.take_action(
            db=self.db, chain_id=chain.id,
            approver_id=self.u1.id, action="approve",
        )
        steps = self.svc.get_chain_steps(db=self.db, chain_id=chain.id)
        self.assertEqual(steps[0].status, "approved")
        self.assertEqual(steps[1].status, "pending")
        self.assertEqual(chain.status, "in_progress")
        self.assertEqual(chain.current_step, 1)

    def test_serial_chain_approve_all_completes(self):
        chain = self._create_serial()
        self.svc.take_action(db=self.db, chain_id=chain.id, approver_id=self.u1.id, action="approve")
        chain = self.svc.take_action(db=self.db, chain_id=chain.id, approver_id=self.u2.id, action="approve")
        self.assertEqual(chain.status, "approved")

    def test_serial_chain_reject_stops(self):
        chain = self._create_serial()
        chain = self.svc.take_action(
            db=self.db, chain_id=chain.id,
            approver_id=self.u1.id, action="reject", note="条款有问题",
        )
        self.assertEqual(chain.status, "rejected")
        steps = self.svc.get_chain_steps(db=self.db, chain_id=chain.id)
        self.assertEqual(steps[0].status, "rejected")
        self.assertEqual(steps[0].note, "条款有问题")

    def test_serial_chain_wrong_approver_raises(self):
        chain = self._create_serial()
        with self.assertRaises(ValueError):
            # u2 的步骤还在 waiting，不能操作
            self.svc.take_action(
                db=self.db, chain_id=chain.id,
                approver_id=self.u2.id, action="approve",
            )

    def test_action_on_rejected_chain_raises(self):
        chain = self._create_serial()
        self.svc.take_action(db=self.db, chain_id=chain.id, approver_id=self.u1.id, action="reject")
        with self.assertRaises(ValueError):
            self.svc.take_action(db=self.db, chain_id=chain.id, approver_id=self.u2.id, action="approve")

    # ── Parallel chain ────────────────────────────────────────────────────────

    def test_parallel_chain_all_steps_pending(self):
        chain = self._create_parallel()
        steps = self.svc.get_chain_steps(db=self.db, chain_id=chain.id)
        self.assertEqual(len(steps), 2)
        self.assertTrue(all(s.status == "pending" for s in steps))

    def test_parallel_chain_partial_approve_stays_in_progress(self):
        chain = self._create_parallel()
        chain = self.svc.take_action(db=self.db, chain_id=chain.id, approver_id=self.u1.id, action="approve")
        self.assertEqual(chain.status, "in_progress")

    def test_parallel_chain_all_approve_completes(self):
        chain = self._create_parallel()
        self.svc.take_action(db=self.db, chain_id=chain.id, approver_id=self.u1.id, action="approve")
        chain = self.svc.take_action(db=self.db, chain_id=chain.id, approver_id=self.u2.id, action="approve")
        self.assertEqual(chain.status, "approved")

    def test_parallel_chain_one_reject_stops(self):
        chain = self._create_parallel()
        chain = self.svc.take_action(db=self.db, chain_id=chain.id, approver_id=self.u1.id, action="reject")
        self.assertEqual(chain.status, "rejected")

    # ── Timeout ───────────────────────────────────────────────────────────────

    def test_timeout_check_marks_overdue_steps(self):
        chain = self._create_serial(timeout=1)
        # 手动把 due_at 设为过去时间
        step = (
            self.db.query(LegalApprovalStep)
            .filter(LegalApprovalStep.chain_id == chain.id, LegalApprovalStep.status == "pending")
            .first()
        )
        step.due_at = datetime.now(timezone.utc) - timedelta(hours=2)
        self.db.commit()

        count = self.svc.run_timeout_check(db=self.db)
        self.assertEqual(count, 1)

        self.db.refresh(step)
        self.assertEqual(step.status, "timeout")
        self.db.refresh(chain)
        self.assertEqual(chain.status, "timeout")

    def test_timeout_check_no_overdue_returns_zero(self):
        chain = self._create_serial(timeout=24)
        count = self.svc.run_timeout_check(db=self.db)
        self.assertEqual(count, 0)

    # ── Queries ───────────────────────────────────────────────────────────────

    def test_get_pending_for_user(self):
        chain = self._create_serial()
        pending = self.svc.get_pending_for_user(db=self.db, user_id=self.u1.id)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].id, chain.id)

    def test_get_pending_excludes_waiting_steps(self):
        self._create_serial()
        # u2 的步骤是 waiting，不应该出现在 pending 列表
        pending = self.svc.get_pending_for_user(db=self.db, user_id=self.u2.id)
        self.assertEqual(len(pending), 0)

    def test_create_chain_empty_approvers_raises(self):
        with self.assertRaises(ValueError):
            self.svc.create_chain(
                db=self.db, org_id=self.org.id,
                target_type="draft", target_id=1,
                chain_type="serial", approvers=[],
                created_by=self.creator.id,
            )

    def test_create_chain_invalid_type_raises(self):
        with self.assertRaises(ValueError):
            self.svc.create_chain(
                db=self.db, org_id=self.org.id,
                target_type="draft", target_id=1,
                chain_type="waterfall", approvers=[{"user_id": self.u1.id}],
                created_by=self.creator.id,
            )


class ApprovalApiTests(unittest.TestCase):
    """集成测试：HTTP 层调用审批链 API。"""

    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        self.db = SessionLocal()

        self.u1 = User(username="api_u1", email="api_u1@x.com", hashed_password=hash_password("pw"), role="user")
        self.u2 = User(username="api_u2", email="api_u2@x.com", hashed_password=hash_password("pw"), role="user")
        self.db.add_all([self.u1, self.u2])
        self.db.commit()
        for u in [self.u1, self.u2]:
            self.db.refresh(u)

        self.org = Organization(name="API律所", code="api_firm")
        self.db.add(self.org)
        self.db.commit()
        self.db.refresh(self.org)

        for u in [self.u1, self.u2]:
            self.db.add(OrganizationMember(
                organization_id=self.org.id, user_id=u.id, legal_role="reviewer"
            ))
        self.db.commit()

        def override_get_db():
            d = SessionLocal()
            try:
                yield d
            finally:
                d.close()

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)
        self.h1 = {"Authorization": f"Bearer {create_access_token({'sub': self.u1.id})}"}
        self.h2 = {"Authorization": f"Bearer {create_access_token({'sub': self.u2.id})}"}

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def test_create_serial_chain_via_api(self):
        resp = self.client.post(
            f"/api/legal/orgs/{self.org.id}/approval-chains",
            json={
                "target_type": "draft",
                "target_id": 10,
                "chain_type": "serial",
                "approvers": [
                    {"user_id": self.u1.id, "role": "reviewer"},
                    {"user_id": self.u2.id, "role": "admin"},
                ],
            },
            headers=self.h1,
        )
        self.assertEqual(resp.status_code, 201)
        body = resp.json()["data"]
        self.assertEqual(body["chain_type"], "serial")
        self.assertEqual(body["status"], "in_progress")
        self.assertEqual(len(body["steps"]), 2)
        self.assertEqual(body["steps"][0]["status"], "pending")
        self.assertEqual(body["steps"][1]["status"], "waiting")

    def test_approve_via_api_advances_chain(self):
        # Create chain
        resp = self.client.post(
            f"/api/legal/orgs/{self.org.id}/approval-chains",
            json={
                "target_type": "draft",
                "target_id": 11,
                "chain_type": "serial",
                "approvers": [{"user_id": self.u1.id}, {"user_id": self.u2.id}],
            },
            headers=self.h1,
        )
        chain_id = resp.json()["data"]["id"]

        # u1 approves
        resp = self.client.post(
            f"/api/legal/approval-chains/{chain_id}/actions",
            json={"action": "approve"},
            headers=self.h1,
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()["data"]
        self.assertEqual(body["status"], "in_progress")
        self.assertEqual(body["current_step"], 1)

    def test_list_pending_for_me(self):
        # Create a chain where u1 is first approver
        self.client.post(
            f"/api/legal/orgs/{self.org.id}/approval-chains",
            json={
                "target_type": "draft", "target_id": 12,
                "chain_type": "serial",
                "approvers": [{"user_id": self.u1.id}],
            },
            headers=self.h1,
        )
        resp = self.client.get("/api/legal/approval-chains/pending", headers=self.h1)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertGreaterEqual(len(data), 1)


if __name__ == "__main__":
    unittest.main()
