"""P1 领域 API 测试：新端点 / 权限隔离 / 旧 API 兼容。

覆盖需求验收点：跨租户/用户隔离(10)、旧 API 与旧数据兼容(11)、发布门禁服务端强制(7/12)。
"""
import json
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import create_access_token, hash_password
from app.core.database import Base, get_db
from app.main import app
from app.models.legal import ContractReview, LegalCase, LegalDraft, LegalSource
from app.models.legal_domain import ContractRiskItem
from app.models.org import Organization, OrganizationMember
from app.models.user import User
from app.services.legal.legal_domain_service import persist_review_artifacts

HIGH_RISK = {
    "clause_type": "breach", "label": "违约责任", "risk_level": "high",
    "description": "违约金过高", "source_location": {"paragraph": 1, "snippet": "甲方违约需支付100%违约金"},
    "suggestion": "降低违约金", "status": "open",
}


class LegalDomainApiTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        self.db = self.TestingSessionLocal()

        self.admin = User(username="lawyer", email="law@x.com", hashed_password=hash_password("pw"), role="admin")
        self.member = User(username="member", email="m@x.com", hashed_password=hash_password("pw"), role="user")
        self.other_user = User(username="other", email="o@x.com", hashed_password=hash_password("pw"), role="user")
        self.db.add_all([self.admin, self.member, self.other_user])
        self.org1 = Organization(name="律所A", code="firm_a")
        self.org2 = Organization(name="律所B", code="firm_b")
        self.db.add_all([self.org1, self.org2])
        self.db.commit()
        for u in (self.admin, self.member, self.other_user):
            self.db.refresh(u)
        self.db.refresh(self.org1)
        self.db.refresh(self.org2)
        self.db.add_all([
            OrganizationMember(organization_id=self.org1.id, user_id=self.admin.id, legal_role="reviewer"),
            OrganizationMember(organization_id=self.org1.id, user_id=self.member.id, legal_role="editor"),
            OrganizationMember(organization_id=self.org2.id, user_id=self.other_user.id, legal_role="editor"),
        ])
        self.case = LegalCase(organization_id=self.org1.id, user_id=self.member.id,
                              title="案件A", case_type="labor_dispute")
        self.db.add(self.case)
        self.source = LegalSource(
            user_id=self.member.id, title="劳动合同法", source_type="statute", content="第40条",
            status="active", jurisdiction="中国大陆",
        )
        self.db.add(self.source)
        self.db.commit()
        self.db.refresh(self.case)
        self.db.refresh(self.source)

        def override_get_db():
            db = self.TestingSessionLocal()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)
        self.admin_headers = {"Authorization": f"Bearer {create_access_token({'sub': self.admin.id})}"}
        self.member_headers = {"Authorization": f"Bearer {create_access_token({'sub': self.member.id})}"}
        self.other_headers = {"Authorization": f"Bearer {create_access_token({'sub': self.other_user.id})}"}

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def _create_review(self, user=None, risks=None, refs=None, status="pending_review", case_id=None):
        user = user or self.member
        review = ContractReview(
            user_id=user.id, case_id=case_id if case_id is not None else self.case.id,
            title="服务合同", content="合同正文", summary="", risks_json="[]",
            references_json="[]", status=status,
        )
        self.db.add(review)
        self.db.commit()
        self.db.refresh(review)
        if risks is not None:
            persist_review_artifacts(self.db, review, risks=risks, refs=refs or [])
        return review

    # ── 案件域聚合 + 跨租户隔离 ────────────────────────────────────────────────

    def test_case_domain_accessible_by_org_member(self):
        response = self.client.get(f"/api/legal/cases/{self.case.id}/domain", headers=self.member_headers)
        self.assertEqual(response.status_code, 200)
        body = response.json()["data"]
        self.assertEqual(body["case_id"], self.case.id)
        for key in ("facts", "evidences", "claims", "references", "risk_items"):
            self.assertIn(key, body)

    def test_case_domain_hidden_across_org(self):
        response = self.client.get(f"/api/legal/cases/{self.case.id}/domain", headers=self.other_headers)
        self.assertEqual(response.status_code, 404)

    # ── 结构化风险项 ───────────────────────────────────────────────────────────

    def test_risk_items_listed_for_owner(self):
        review = self._create_review(risks=[HIGH_RISK])
        response = self.client.get(f"/api/legal/contract-reviews/{review.id}/risk-items", headers=self.member_headers)
        self.assertEqual(response.status_code, 200)
        items = response.json()["data"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["severity"], "high")
        self.assertEqual(items[0]["status"], "needs_review")

    def test_risk_items_forbidden_for_other_user(self):
        review = self._create_review(risks=[HIGH_RISK])
        response = self.client.get(f"/api/legal/contract-reviews/{review.id}/risk-items", headers=self.other_headers)
        self.assertEqual(response.status_code, 403)

    def test_risk_item_action_requires_reviewer_role(self):
        review = self._create_review(risks=[HIGH_RISK])
        item = self.db.query(ContractRiskItem).first()
        response = self.client.post(
            f"/api/legal/contract-reviews/{review.id}/risk-items/{item.id}/action",
            json={"action": "accept", "note": "ok"}, headers=self.member_headers,
        )
        self.assertEqual(response.status_code, 403)

    def test_risk_item_action_by_admin(self):
        review = self._create_review(risks=[HIGH_RISK])
        item = self.db.query(ContractRiskItem).first()
        response = self.client.post(
            f"/api/legal/contract-reviews/{review.id}/risk-items/{item.id}/action",
            json={"action": "accept", "note": "已接受"}, headers=self.admin_headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["status"], "accepted")

    def test_risk_item_action_unknown_action_rejected(self):
        review = self._create_review(risks=[HIGH_RISK])
        item = self.db.query(ContractRiskItem).first()
        response = self.client.post(
            f"/api/legal/contract-reviews/{review.id}/risk-items/{item.id}/action",
            json={"action": "explode"}, headers=self.admin_headers,
        )
        self.assertEqual(response.status_code, 400)

    # ── 发布门禁（服务端强制）──────────────────────────────────────────────────

    def test_publish_blocked_when_not_approved(self):
        review = self._create_review(risks=[HIGH_RISK])
        response = self.client.post(f"/api/legal/contract-reviews/{review.id}/publish", headers=self.admin_headers)
        self.assertEqual(response.status_code, 409)

    def test_publish_blocked_when_high_risk_unresolved_after_approve(self):
        review = self._create_review(risks=[HIGH_RISK], status="needs_lawyer_review")
        # 审核通过但高风险未处理 → 仍不可发布
        self.client.post(
            f"/api/legal/review-queue/contract_review/{review.id}/actions",
            json={"action": "approve", "note": "ok"}, headers=self.admin_headers,
        )
        response = self.client.post(f"/api/legal/contract-reviews/{review.id}/publish", headers=self.admin_headers)
        self.assertEqual(response.status_code, 409)

    def test_publish_succeeds_after_approve_and_resolve(self):
        review = self._create_review(risks=[HIGH_RISK], status="needs_lawyer_review")
        self.client.post(
            f"/api/legal/review-queue/contract_review/{review.id}/actions",
            json={"action": "approve", "note": "ok"}, headers=self.admin_headers,
        )
        item = self.db.query(ContractRiskItem).first()
        self.client.post(
            f"/api/legal/contract-reviews/{review.id}/risk-items/{item.id}/action",
            json={"action": "accept", "note": "已接受"}, headers=self.admin_headers,
        )
        response = self.client.post(f"/api/legal/contract-reviews/{review.id}/publish", headers=self.admin_headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["data"]["is_final"])

    # ── 文书定稿门禁 ───────────────────────────────────────────────────────────

    def test_mark_final_blocked_when_not_approved(self):
        draft = LegalDraft(
            user_id=self.member.id, case_id=self.case.id, document_type="labor_arbitration_application",
            title="仲裁申请书", fields_json="{}", missing_fields_json="[]",
            references_json="[]", content="草稿", version=1, status="pending_review",
        )
        self.db.add(draft)
        self.db.commit()
        self.db.refresh(draft)
        response = self.client.post(f"/api/legal/drafts/{draft.id}/mark-final", headers=self.member_headers)
        self.assertEqual(response.status_code, 409)

    # ── 主张追溯 ───────────────────────────────────────────────────────────────

    def test_claims_endpoint(self):
        review = self._create_review(risks=[HIGH_RISK])
        response = self.client.get(f"/api/legal/contract-reviews/{review.id}/claims", headers=self.member_headers)
        self.assertEqual(response.status_code, 200)
        claims = response.json()["data"]
        self.assertGreaterEqual(len(claims), 1)
        self.assertEqual(claims[0]["claim_type"], "risk_warning")

    # ── 旧 API 兼容：旧数据在新增字段缺失时仍可正常读取 ────────────────────────

    def test_old_data_reads_with_new_fields_defaulted(self):
        # 旧数据：risks_json 有内容，但无结构化表、无 is_final/model_snapshot 列值
        review = ContractReview(
            user_id=self.member.id, case_id=self.case.id, title="旧合同", content="旧正文",
            summary="旧摘要", risks_json=json.dumps([HIGH_RISK]), references_json="[]",
            status="lawyer_approved", version=1,
        )
        self.db.add(review)
        self.db.commit()
        self.db.refresh(review)
        response = self.client.get(f"/api/legal/contract-reviews/{review.id}", headers=self.member_headers)
        self.assertEqual(response.status_code, 200)
        body = response.json()["data"]
        self.assertEqual(len(body["risks"]), 1)              # 旧 JSON 风险仍可读
        self.assertFalse(body["is_final"])                    # 新字段安全默认值
        self.assertIsNone(body["reviewed_version"])
        self.assertEqual(body["model_snapshot"], {})


if __name__ == "__main__":
    unittest.main()
