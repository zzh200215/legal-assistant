"""工作台记录详情可见性边界（ux-audit 方案 A）：本人、同组织审核律师、被指派审核人、
平台管理员可读他人记录；跨组织一律按不存在处理；关联严格模式案件须为案件成员。"""

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


class LegalWorkspaceVisibilityTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(engine)
        self.db = self.session_factory()
        self.org1 = Organization(name="组织一", code="vis_org1")
        self.org2 = Organization(name="组织二", code="vis_org2")
        self.db.add_all([self.org1, self.org2])
        self.db.flush()

        def mk(username, org, role="user"):
            u = User(username=username, email=f"{username}@example.com", hashed_password=hash_password("pw"), organization_id=org.id, status=UserStatus.active.value, role=role)
            self.db.add(u)
            return u

        self.owner = mk("vis-owner", self.org1)
        self.reviewer1 = mk("vis-reviewer1", self.org1)
        self.editorx = mk("vis-editorx", self.org1)
        self.reviewer2 = mk("vis-reviewer2", self.org2)
        self.admin = mk("vis-admin", self.org2, role="admin")
        self.db.flush()
        self.db.add_all([
            OrganizationMember(organization_id=self.org1.id, user_id=self.owner.id, legal_role=LegalMemberRole.editor.value),
            OrganizationMember(organization_id=self.org1.id, user_id=self.reviewer1.id, legal_role=LegalMemberRole.reviewer.value),
            OrganizationMember(organization_id=self.org1.id, user_id=self.editorx.id, legal_role=LegalMemberRole.editor.value),
            OrganizationMember(organization_id=self.org2.id, user_id=self.reviewer2.id, legal_role=LegalMemberRole.reviewer.value),
            OrganizationMember(organization_id=self.org2.id, user_id=self.admin.id, legal_role=LegalMemberRole.admin.value),
        ])
        self.row = LegalConsultation(user_id=self.owner.id, question="他人咨询", category="other", known_facts_json="[]", missing_facts_json="[]", references_json="[]", advice="建议", risk_level="medium", status="needs_lawyer_review")
        self.db.add(self.row)
        self.db.commit()
        self.db.refresh(self.row)
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def _read(self, user, item_id=None):
        headers = {"Authorization": f"Bearer {create_access_token({'sub': user.id})}"}
        return self.client.get(f"/api/legal/consultations/{item_id or self.row.id}", headers=headers)

    def test_owner_reads_own(self):
        self.assertEqual(self._read(self.owner).status_code, 200)

    def test_same_org_reviewer_reads_others(self):
        self.assertEqual(self._read(self.reviewer1).status_code, 200)

    def test_same_org_non_reviewer_denied(self):
        self.assertEqual(self._read(self.editorx).status_code, 404)

    def test_cross_org_reviewer_denied(self):
        self.assertEqual(self._read(self.reviewer2).status_code, 404)

    def test_platform_admin_reads_cross_org(self):
        self.assertEqual(self._read(self.admin).status_code, 200)

    def test_assigned_reviewer_reads(self):
        self.row.reviewer_id = self.editorx.id
        self.db.commit()
        self.assertEqual(self._read(self.editorx).status_code, 200)

    def test_strict_case_blocks_non_member_reviewer(self):
        strict_case = LegalCase(title="严格案件", case_type="other", organization_id=self.org1.id, user_id=self.owner.id, is_strict_mode=1)
        self.db.add(strict_case)
        self.db.flush()
        self.row.case_id = strict_case.id
        self.db.commit()
        self.assertEqual(self._read(self.reviewer1).status_code, 404)
        self.assertEqual(self._read(self.owner).status_code, 200)


if __name__ == "__main__":
    unittest.main()
