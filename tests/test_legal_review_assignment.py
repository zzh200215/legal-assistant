"""审核任务分配、截止时间与权限契约。"""

import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import create_access_token, hash_password
from app.core.database import Base, get_db
from app.main import app
from app.models.legal import LegalConsultation
from app.models.org import LegalMemberRole, Organization, OrganizationMember
from app.models.user import User, UserStatus


class LegalReviewAssignmentTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(engine)
        self.db = self.session_factory()
        org = Organization(name="审核组织", code="review_assignment")
        self.db.add(org)
        self.db.flush()
        self.org = org
        self.admin = User(username="review-admin", email="review-admin@example.com", hashed_password=hash_password("pw"), organization_id=org.id, status=UserStatus.active.value)
        self.reviewer = User(username="reviewer", email="reviewer@example.com", hashed_password=hash_password("pw"), organization_id=org.id, status=UserStatus.active.value)
        self.client_user = User(username="review-client", email="review-client@example.com", hashed_password=hash_password("pw"), organization_id=org.id, status=UserStatus.active.value)
        self.db.add_all([self.admin, self.reviewer, self.client_user])
        self.db.flush()
        self.db.add_all([
            OrganizationMember(organization_id=org.id, user_id=self.admin.id, legal_role=LegalMemberRole.admin.value),
            OrganizationMember(organization_id=org.id, user_id=self.reviewer.id, legal_role=LegalMemberRole.reviewer.value),
            OrganizationMember(organization_id=org.id, user_id=self.client_user.id, legal_role=LegalMemberRole.editor.value),
        ])
        self.row = LegalConsultation(user_id=self.client_user.id, question="待审核问题", category="other", known_facts_json="[]", missing_facts_json="[]", references_json="[]", advice="建议", risk_level="medium", status="needs_lawyer_review")
        self.db.add(self.row)
        self.db.commit()
        self.db.refresh(self.row)
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)
        self.admin_headers = {"Authorization": f"Bearer {create_access_token({'sub': self.admin.id})}"}
        self.reviewer_headers = {"Authorization": f"Bearer {create_access_token({'sub': self.reviewer.id})}"}
        self.editor_headers = {"Authorization": f"Bearer {create_access_token({'sub': self.client_user.id})}"}

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def test_admin_assigns_reviewer_and_due_date(self):
        response = self.client.patch(
            f"/api/legal/review-queue/consultation/{self.row.id}/assignment",
            json={"reviewer_id": self.reviewer.id, "due_at": "2026-10-06T12:00:00Z"},
            headers=self.admin_headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()["data"]
        self.assertEqual(body["reviewer_id"], self.reviewer.id)
        self.assertIsNotNone(body["review_due_at"])

        queue = self.client.get("/api/legal/review-queue", headers=self.admin_headers)
        self.assertEqual(queue.status_code, 200)
        self.assertEqual(queue.json()["data"][0]["reviewer_id"], self.reviewer.id)

    def test_editor_cannot_assign_and_invalid_assignee_is_rejected(self):
        forbidden = self.client.patch(
            f"/api/legal/review-queue/consultation/{self.row.id}/assignment",
            json={"reviewer_id": self.reviewer.id}, headers=self.editor_headers,
        )
        self.assertEqual(forbidden.status_code, 403)
        invalid = self.client.patch(
            f"/api/legal/review-queue/consultation/{self.row.id}/assignment",
            json={"reviewer_id": self.client_user.id}, headers=self.admin_headers,
        )
        self.assertEqual(invalid.status_code, 400)

    def test_reviewer_directory_is_organization_scoped(self):
        response = self.client.get("/api/legal/review-queue/reviewers", headers=self.admin_headers)
        self.assertEqual(response.status_code, 200)
        ids = {item["user_id"] for item in response.json()["data"]}
        self.assertEqual(ids, {self.admin.id, self.reviewer.id})

    def test_reviewer_can_review_and_bulk_assign(self):
        queue = self.client.get("/api/legal/review-queue", headers=self.reviewer_headers)
        self.assertEqual(queue.status_code, 200, queue.text)
        self.assertEqual(queue.json()["data"][0]["id"], self.row.id)

        bulk = self.client.post(
            "/api/legal/review-queue/bulk-assignment",
            json={"items": [{"target_type": "consultation", "target_id": self.row.id}], "reviewer_id": self.reviewer.id},
            headers=self.admin_headers,
        )
        self.assertEqual(bulk.status_code, 200, bulk.text)

        action = self.client.post(
            "/api/legal/review-queue/bulk-action",
            json={"items": [{"target_type": "consultation", "target_id": self.row.id}], "action": "approve", "note": "批量审核"},
            headers=self.reviewer_headers,
        )
        self.assertEqual(action.status_code, 200, action.text)
        self.assertEqual(action.json()["data"][0]["status"], "lawyer_approved")

    def test_review_queue_filters_and_sla(self):
        self.row.review_due_at = __import__("datetime").datetime(2020, 1, 1)
        self.db.commit()
        overdue = self.client.get("/api/legal/review-queue?overdue=true", headers=self.admin_headers)
        self.assertEqual(overdue.status_code, 200, overdue.text)
        self.assertEqual(len(overdue.json()["data"]), 1)
        stats = self.client.get("/api/legal/review-stats", headers=self.reviewer_headers)
        self.assertEqual(stats.status_code, 200, stats.text)
        self.assertIn("sla", stats.json()["data"])
        self.assertEqual(stats.json()["data"]["sla"]["overdue_count"], 1)
        self.assertEqual(stats.json()["data"]["sla"]["due_soon_count"], 0)

    def test_submit_review_notifies_reviewers_for_uncited_consultation(self):
        """无依据咨询提交审核 → 同组织审核律师收到站内通知；提交人自己收不到。"""
        from app.models.legal_notifications import LegalNotificationEvent

        response = self.client.post(
            f"/api/legal/review-queue/consultation/{self.row.id}/actions",
            json={"action": "submit_review", "note": "提交审核"}, headers=self.editor_headers,
        )
        self.assertEqual(response.status_code, 200, response.text)

        events = self.db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.user_id == self.reviewer.id,
        ).all()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].title, "无依据咨询待审核")
        self.assertEqual(events[0].reference_type, "consultation")
        self.assertEqual(events[0].reference_id, self.row.id)
        self.assertIn("没有关联法源引用", events[0].body)
        self.assertEqual(
            self.db.query(LegalNotificationEvent).filter(
                LegalNotificationEvent.user_id == self.client_user.id,
            ).count(), 0,
        )

        # 通知中心接口能查到
        notifications = self.client.get("/api/developer/notifications/me", headers=self.reviewer_headers)
        self.assertEqual(notifications.status_code, 200, notifications.text)
        titles = [item["title"] for item in notifications.json()["data"]["items"]]
        self.assertIn("无依据咨询待审核", titles)

    def test_submit_review_with_citations_does_not_notify(self):
        import json as _json

        from app.models.legal_notifications import LegalNotificationEvent

        self.row.references_json = _json.dumps([{"title": "劳动合同法", "status": "active"}], ensure_ascii=False)
        self.row.status = "draft"
        self.db.commit()
        response = self.client.post(
            f"/api/legal/review-queue/consultation/{self.row.id}/actions",
            json={"action": "submit_review", "note": "提交审核"}, headers=self.editor_headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            self.db.query(LegalNotificationEvent).filter(
                LegalNotificationEvent.user_id == self.reviewer.id,
            ).count(), 0,
        )

    def test_reviewer_can_return_uncited_consultation(self):
        """通知里的「要求补充依据」走的就是 return 动作：审核律师可退回，状态与历史可查。"""
        response = self.client.post(
            f"/api/legal/review-queue/consultation/{self.row.id}/actions",
            json={"action": "return", "note": "无依据咨询：请补充法源依据后重新提交审核"},
            headers=self.reviewer_headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["status"], "returned_for_facts")

        history = self.client.get(
            f"/api/legal/review-queue/consultation/{self.row.id}/history", headers=self.reviewer_headers,
        )
        self.assertEqual(history.status_code, 200, history.text)
        entries = history.json()["data"]["history"]
        self.assertEqual(entries[0]["action"], "return")
        self.assertIn("补充法源依据", entries[0]["note"])

    def test_due_soon_is_flagged_and_filterable(self):
        """24 小时内到期：sla_state=due_soon、给出剩余小时数、可按 sla=due_soon 过滤。"""
        from datetime import timedelta

        from app.core.time import utc_now

        self.row.review_due_at = utc_now() + timedelta(hours=6)
        self.db.commit()

        queue = self.client.get("/api/legal/review-queue", headers=self.admin_headers)
        self.assertEqual(queue.status_code, 200, queue.text)
        item = queue.json()["data"][0]
        self.assertEqual(item["sla_state"], "due_soon")
        self.assertFalse(item["review_overdue"])
        self.assertTrue(5 < item["review_due_in_hours"] < 7)

        filtered = self.client.get("/api/legal/review-queue?sla=due_soon", headers=self.admin_headers)
        self.assertEqual(len(filtered.json()["data"]), 1)
        overdue_only = self.client.get("/api/legal/review-queue?sla=overdue", headers=self.admin_headers)
        self.assertEqual(overdue_only.json()["data"], [])

        stats = self.client.get("/api/legal/review-stats", headers=self.reviewer_headers)
        self.assertEqual(stats.json()["data"]["sla"]["due_soon_count"], 1)

    def test_invalid_sla_filter_rejected(self):
        response = self.client.get("/api/legal/review-queue?sla=whatever", headers=self.admin_headers)
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
