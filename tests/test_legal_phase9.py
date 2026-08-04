"""Phase 9 tests: OrganizationMember management + LegalCase CRUD"""
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
from app.models.org import Organization, OrganizationMember, LegalMemberRole
from app.models.user import User


class Phase9Tests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        self.db = self.SessionLocal()

        # Users
        self.admin_user = User(
            username="p9_admin", email="p9_admin@ex.com",
            hashed_password=hash_password("pw"), role="admin",
        )
        self.reviewer_user = User(
            username="p9_reviewer", email="p9_reviewer@ex.com",
            hashed_password=hash_password("pw"), role="user",
        )
        self.client_user = User(
            username="p9_client", email="p9_client@ex.com",
            hashed_password=hash_password("pw"), role="user",
        )
        self.outsider = User(
            username="p9_outsider", email="p9_outsider@ex.com",
            hashed_password=hash_password("pw"), role="user",
        )
        self.db.add_all([self.admin_user, self.reviewer_user, self.client_user, self.outsider])
        self.db.commit()
        for u in [self.admin_user, self.reviewer_user, self.client_user, self.outsider]:
            self.db.refresh(u)

        # Organization
        self.org = Organization(name="测试律所", code="test_law_firm")
        self.db.add(self.org)
        self.db.commit()
        self.db.refresh(self.org)

        # Admin member
        self.admin_member = OrganizationMember(
            organization_id=self.org.id,
            user_id=self.admin_user.id,
            legal_role=LegalMemberRole.admin.value,
        )
        self.db.add(self.admin_member)
        self.db.commit()
        self.db.refresh(self.admin_member)

        def override_get_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)
        self.admin_h = {"Authorization": f"Bearer {create_access_token({'sub': self.admin_user.id})}"}
        self.reviewer_h = {"Authorization": f"Bearer {create_access_token({'sub': self.reviewer_user.id})}"}
        self.client_h = {"Authorization": f"Bearer {create_access_token({'sub': self.client_user.id})}"}
        self.outsider_h = {"Authorization": f"Bearer {create_access_token({'sub': self.outsider.id})}"}

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    # ── Member management ─────────────────────────────────────────────────────

    def test_list_members_returns_admin(self):
        resp = self.client.get(f"/api/legal/orgs/{self.org.id}/members", headers=self.admin_h)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["legal_role"], "admin")

    def test_invite_member_by_admin(self):
        resp = self.client.post(
            f"/api/legal/orgs/{self.org.id}/members",
            json={"user_id": self.reviewer_user.id, "legal_role": "reviewer"},
            headers=self.admin_h,
        )
        self.assertEqual(resp.status_code, 201)
        body = resp.json()["data"]
        self.assertEqual(body["legal_role"], "reviewer")
        self.assertEqual(body["user_id"], self.reviewer_user.id)

    def test_invite_requires_admin_role(self):
        # First make reviewer_user a member (editor role)
        self.db.add(OrganizationMember(
            organization_id=self.org.id,
            user_id=self.reviewer_user.id,
            legal_role="editor",
        ))
        self.db.commit()
        # Editor should not be able to invite
        resp = self.client.post(
            f"/api/legal/orgs/{self.org.id}/members",
            json={"user_id": self.client_user.id, "legal_role": "client"},
            headers=self.reviewer_h,
        )
        self.assertEqual(resp.status_code, 403)

    def test_outsider_cannot_list_members(self):
        resp = self.client.get(f"/api/legal/orgs/{self.org.id}/members", headers=self.outsider_h)
        self.assertEqual(resp.status_code, 403)

    def test_invite_duplicate_member_returns_409(self):
        # Admin is already a member
        resp = self.client.post(
            f"/api/legal/orgs/{self.org.id}/members",
            json={"user_id": self.admin_user.id, "legal_role": "editor"},
            headers=self.admin_h,
        )
        self.assertEqual(resp.status_code, 409)

    def test_update_member_role(self):
        member = OrganizationMember(
            organization_id=self.org.id,
            user_id=self.reviewer_user.id,
            legal_role="editor",
        )
        self.db.add(member)
        self.db.commit()
        self.db.refresh(member)
        resp = self.client.patch(
            f"/api/legal/orgs/{self.org.id}/members/{member.id}",
            json={"legal_role": "reviewer"},
            headers=self.admin_h,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["legal_role"], "reviewer")

    def test_remove_member(self):
        member = OrganizationMember(
            organization_id=self.org.id,
            user_id=self.client_user.id,
            legal_role="client",
        )
        self.db.add(member)
        self.db.commit()
        self.db.refresh(member)
        resp = self.client.delete(
            f"/api/legal/orgs/{self.org.id}/members/{member.id}",
            headers=self.admin_h,
        )
        self.assertEqual(resp.status_code, 204)
        remaining = self.db.query(OrganizationMember).filter(
            OrganizationMember.id == member.id
        ).first()
        self.assertIsNone(remaining)

    # ── Case management ───────────────────────────────────────────────────────

    def _add_reviewer_member(self):
        m = OrganizationMember(
            organization_id=self.org.id,
            user_id=self.reviewer_user.id,
            legal_role="reviewer",
        )
        self.db.add(m)
        self.db.commit()
        return m

    def _add_client_member(self):
        m = OrganizationMember(
            organization_id=self.org.id,
            user_id=self.client_user.id,
            legal_role="client",
        )
        self.db.add(m)
        self.db.commit()
        return m

    def test_create_case_by_editor_or_above(self):
        resp = self.client.post(
            f"/api/legal/orgs/{self.org.id}/cases",
            json={
                "organization_id": self.org.id,
                "title": "张三劳动争议案",
                "case_type": "labor_dispute",
                "client_name": "张三",
            },
            headers=self.admin_h,
        )
        self.assertEqual(resp.status_code, 201)
        body = resp.json()["data"]
        self.assertEqual(body["title"], "张三劳动争议案")
        self.assertEqual(body["case_type"], "labor_dispute")
        self.assertEqual(body["status"], "in_progress")

    def test_client_role_cannot_create_case(self):
        self._add_client_member()
        resp = self.client.post(
            f"/api/legal/orgs/{self.org.id}/cases",
            json={
                "organization_id": self.org.id,
                "title": "测试案件",
                "case_type": "other",
            },
            headers=self.client_h,
        )
        self.assertEqual(resp.status_code, 403)

    def test_outsider_cannot_create_case(self):
        resp = self.client.post(
            f"/api/legal/orgs/{self.org.id}/cases",
            json={
                "organization_id": self.org.id,
                "title": "测试案件",
                "case_type": "other",
            },
            headers=self.outsider_h,
        )
        self.assertEqual(resp.status_code, 403)

    def test_list_cases_returns_created(self):
        # Create a case directly in DB
        case = LegalCase(
            organization_id=self.org.id,
            user_id=self.admin_user.id,
            title="合同纠纷案",
            case_type="contract_dispute",
        )
        self.db.add(case)
        self.db.commit()

        resp = self.client.get(f"/api/legal/orgs/{self.org.id}/cases", headers=self.admin_h)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["title"], "合同纠纷案")

    def test_get_case_includes_item_counts(self):
        case = LegalCase(
            organization_id=self.org.id,
            user_id=self.admin_user.id,
            title="测试案件",
            case_type="other",
        )
        self.db.add(case)
        self.db.commit()
        self.db.refresh(case)

        # Add a consultation linked to this case
        consultation = LegalConsultation(
            user_id=self.admin_user.id,
            case_id=case.id,
            question="测试问题",
            category="other",
            known_facts_json="[]",
            missing_facts_json="[]",
            references_json="[]",
            advice="建议",
        )
        self.db.add(consultation)
        self.db.commit()

        resp = self.client.get(
            f"/api/legal/orgs/{self.org.id}/cases/{case.id}",
            headers=self.admin_h,
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()["data"]
        self.assertEqual(body["item_counts"]["consultations"], 1)
        self.assertEqual(body["item_counts"]["reviews"], 0)

    def test_update_case_status(self):
        case = LegalCase(
            organization_id=self.org.id,
            user_id=self.admin_user.id,
            title="待结案件",
            case_type="other",
        )
        self.db.add(case)
        self.db.commit()
        self.db.refresh(case)

        resp = self.client.patch(
            f"/api/legal/orgs/{self.org.id}/cases/{case.id}",
            json={"status": "closed"},
            headers=self.admin_h,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["status"], "closed")

    def test_get_case_items(self):
        case = LegalCase(
            organization_id=self.org.id,
            user_id=self.admin_user.id,
            title="项目列表测试案件",
            case_type="other",
        )
        self.db.add(case)
        self.db.commit()
        self.db.refresh(case)

        self.db.add(LegalConsultation(
            user_id=self.admin_user.id,
            case_id=case.id,
            question="我有个法律问题",
            category="other",
            known_facts_json="[]",
            missing_facts_json="[]",
            references_json="[]",
            advice="建议",
        ))
        self.db.commit()

        resp = self.client.get(
            f"/api/legal/orgs/{self.org.id}/cases/{case.id}/items",
            headers=self.admin_h,
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()["data"]
        self.assertEqual(len(body["consultations"]), 1)
        self.assertEqual(body["consultations"][0]["category"], "other")

    def test_case_not_found_returns_404(self):
        resp = self.client.get(
            f"/api/legal/orgs/{self.org.id}/cases/99999",
            headers=self.admin_h,
        )
        self.assertEqual(resp.status_code, 404)

    def test_invalid_case_type_returns_400(self):
        resp = self.client.post(
            f"/api/legal/orgs/{self.org.id}/cases",
            json={
                "organization_id": self.org.id,
                "title": "测试",
                "case_type": "invalid_type",
            },
            headers=self.admin_h,
        )
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
