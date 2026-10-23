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
        self.assertEqual(events[0].template_key, "review_consultation_uncited")
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

    def test_submit_review_notifies_for_weak_evidence(self):
        """依据偏弱（有引用但句级覆盖 <50%）也提醒审核律师，标题与模板键区分于无依据。"""
        import json as _json

        from app.models.legal_notifications import LegalNotificationEvent

        self.row.references_json = _json.dumps([{"title": "劳动合同法", "status": "active"}], ensure_ascii=False)
        self.row.attribution_json = _json.dumps({"total_sentences": 10, "matched_sentences": 2})
        self.row.status = "draft"
        self.db.commit()
        response = self.client.post(
            f"/api/legal/review-queue/consultation/{self.row.id}/actions",
            json={"action": "submit_review", "note": "提交审核"}, headers=self.editor_headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        events = self.db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.user_id == self.reviewer.id,
        ).all()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].title, "依据偏弱的咨询待审核")
        self.assertEqual(events[0].template_key, "review_consultation_weak")
        self.assertIn("句级溯源 2/10 句", events[0].body)

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

        # 反向通知：提交人收到「需要补充依据」
        from app.models.legal_notifications import LegalNotificationEvent

        events = self.db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.user_id == self.client_user.id,
        ).all()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].title, "咨询被退回：需要补充依据")
        self.assertEqual(events[0].template_key, "consultation_returned_supplement")
        self.assertEqual(events[0].reference_type, "consultation")
        self.assertEqual(events[0].reference_id, self.row.id)
        self.assertIn("没有关联法源引用", events[0].body)
        # 审核律师自己不会收到反向通知
        self.assertEqual(
            self.db.query(LegalNotificationEvent).filter(
                LegalNotificationEvent.user_id == self.reviewer.id,
            ).count(), 0,
        )

    def test_return_with_citations_sends_generic_review_note(self):
        import json as _json

        from app.models.legal_notifications import LegalNotificationEvent

        self.row.references_json = _json.dumps([{"title": "劳动合同法", "status": "active"}], ensure_ascii=False)
        self.db.commit()
        response = self.client.post(
            f"/api/legal/review-queue/consultation/{self.row.id}/actions",
            json={"action": "return", "note": "事实描述需要补充时间线"},
            headers=self.reviewer_headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        events = self.db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.user_id == self.client_user.id,
        ).all()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].title, "咨询被退回，请查看审核意见")
        self.assertEqual(events[0].template_key, "consultation_returned")
        self.assertIn("事实描述需要补充时间线", events[0].body)

    def test_supplement_after_return_notifies_reviewer_with_comparison(self):
        """退回后补充：审核律师收到「引用 +1，覆盖 75%」的通知。"""
        import asyncio
        from unittest.mock import AsyncMock, patch

        from app.models.legal_notifications import LegalNotificationEvent
        from app.services.legal.legal_workspace_service import legal_workspace_module

        self.row.status = "returned_for_facts"
        self.db.commit()
        parent_id = self.row.id
        reviewer_id = self.reviewer.id

        followup_result = (
            "labor", [], [], [{"title": "劳动合同法", "source_id": 1, "status": "active"}],
            "补充答复", "low", "draft",
        )
        attribution = {"total_sentences": 4, "matched_sentences": 3}
        with patch(
            "app.services.legal.legal_workspace_service.consultation_followup",
            new=AsyncMock(return_value=followup_result),
        ), patch(
            "app.services.legal.legal_workspace_service.attribute_sentences_to_references",
            return_value=attribution,
        ), patch(
            "app.services.legal.legal_workspace_service.subscription_service",
        ) as subscription:
            subscription.check_quota.return_value = True
            row = asyncio.run(legal_workspace_module.create_consultation_followup(
                self.db, self.client_user, consultation_id=self.row.id, question="补充材料",
            ))

        self.assertEqual(row.parent_id, parent_id)
        events = self.db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.user_id == reviewer_id,
        ).all()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].title, "咨询已补充依据：引用 +1，覆盖 75%")
        self.assertEqual(events[0].template_key, "consultation_supplemented")
        self.assertEqual(events[0].reference_id, row.id)
        self.assertIn("引用 0 → 1", events[0].body)

    def test_followup_without_return_does_not_notify(self):
        import asyncio
        from unittest.mock import AsyncMock, patch

        from app.models.legal_notifications import LegalNotificationEvent
        from app.services.legal.legal_workspace_service import legal_workspace_module

        self.reviewer_id_cached = self.reviewer.id
        followup_result = (
            "labor", [], [], [{"title": "劳动合同法", "source_id": 1, "status": "active"}],
            "补充答复", "low", "draft",
        )
        with patch(
            "app.services.legal.legal_workspace_service.consultation_followup",
            new=AsyncMock(return_value=followup_result),
        ), patch(
            "app.services.legal.legal_workspace_service.attribute_sentences_to_references",
            return_value={"total_sentences": 4, "matched_sentences": 3},
        ), patch(
            "app.services.legal.legal_workspace_service.subscription_service",
        ) as subscription:
            subscription.check_quota.return_value = True
            asyncio.run(legal_workspace_module.create_consultation_followup(
                self.db, self.client_user, consultation_id=self.row.id, question="再问一句",
            ))
        self.assertEqual(
            self.db.query(LegalNotificationEvent).filter(
                LegalNotificationEvent.user_id == self.reviewer_id_cached,
            ).count(), 0,
        )

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
