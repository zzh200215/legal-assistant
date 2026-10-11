"""孤立待审核记录归入案件（ux-audit L-17）的权限与行为契约。"""

import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import create_access_token, hash_password
from app.core.database import Base, get_db
from app.main import app
from app.models.legal import LegalCase, LegalConsultation
from app.models.org import LegalMemberRole, Organization, OrganizationMember
from app.models.user import User, UserStatus


class LegalReviewCaseAssociationTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(engine)
        self.db = self.session_factory()
        org = Organization(name="归入组织", code="assoc_case")
        self.db.add(org)
        self.db.flush()
        self.org = org
        self.reviewer = User(username="assoc-reviewer", email="assoc-reviewer@example.com", hashed_password=hash_password("pw"), organization_id=org.id, status=UserStatus.active.value)
        self.owner = User(username="assoc-owner", email="assoc-owner@example.com", hashed_password=hash_password("pw"), organization_id=org.id, status=UserStatus.active.value)
        self.other = User(username="assoc-other", email="assoc-other@example.com", hashed_password=hash_password("pw"), organization_id=org.id, status=UserStatus.active.value)
        self.db.add_all([self.reviewer, self.owner, self.other])
        self.db.flush()
        self.db.add_all([
            OrganizationMember(organization_id=org.id, user_id=self.reviewer.id, legal_role=LegalMemberRole.reviewer.value),
            OrganizationMember(organization_id=org.id, user_id=self.owner.id, legal_role=LegalMemberRole.editor.value),
            OrganizationMember(organization_id=org.id, user_id=self.other.id, legal_role=LegalMemberRole.editor.value),
        ])
        self.case = LegalCase(title="归档目标案件", case_type="other", organization_id=org.id, user_id=self.owner.id)
        self.db.add(self.case)
        self.row = LegalConsultation(user_id=self.owner.id, question="孤立咨询", category="other", known_facts_json="[]", missing_facts_json="[]", references_json="[]", advice="建议", risk_level="medium", status="needs_lawyer_review")
        self.db.add(self.row)
        self.db.commit()
        self.db.refresh(self.case)
        self.db.refresh(self.row)
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)
        self.reviewer_headers = {"Authorization": f"Bearer {create_access_token({'sub': self.reviewer.id})}"}
        self.owner_headers = {"Authorization": f"Bearer {create_access_token({'sub': self.owner.id})}"}
        self.other_headers = {"Authorization": f"Bearer {create_access_token({'sub': self.other.id})}"}

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def _associate(self, headers, case_id):
        return self.client.patch(f"/api/legal/review-queue/consultation/{self.row.id}/case", json={"case_id": case_id}, headers=headers)

    def test_owner_associates_orphan_record_to_case(self):
        self.assertIsNone(self.row.case_id)
        resp = self._associate(self.owner_headers, self.case.id)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["data"]["case_id"], self.case.id)
        self.db.refresh(self.row)
        self.assertEqual(self.row.case_id, self.case.id)

    def test_reviewer_can_associate(self):
        resp = self._associate(self.reviewer_headers, self.case.id)
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_non_owner_non_reviewer_forbidden(self):
        resp = self._associate(self.other_headers, self.case.id)
        self.assertEqual(resp.status_code, 403, resp.text)
        self.db.refresh(self.row)
        self.assertIsNone(self.row.case_id)

    def test_case_outside_org_rejected(self):
        other_org = Organization(name="外部组织", code="assoc_other_org")
        self.db.add(other_org)
        self.db.flush()
        foreign_case = LegalCase(title="外部案件", case_type="other", organization_id=other_org.id, user_id=self.owner.id)
        self.db.add(foreign_case)
        self.db.commit()
        resp = self._associate(self.owner_headers, foreign_case.id)
        self.assertEqual(resp.status_code, 400, resp.text)

    def test_missing_target_returns_404(self):
        resp = self.client.patch("/api/legal/review-queue/consultation/999999/case", json={"case_id": self.case.id}, headers=self.owner_headers)
        self.assertEqual(resp.status_code, 404, resp.text)


if __name__ == "__main__":
    unittest.main()
