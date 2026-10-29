"""案件级协作可见性（ux-audit 方案 B）：案件成员可读/列出同案件下其他成员的工作台记录；
个人默认视图维持隔离；跨组织与严格模式案件按既有 verify_case_access 口径收口。"""

import unittest
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import create_access_token, hash_password
from app.core.database import Base, get_db
from app.core.time import utc_now
from app.main import app
from app.models.legal import LegalCase, LegalConsultation, LegalConsultationExport
from app.models.legal_portal import LegalCaseMember
from app.models.org import LegalMemberRole, Organization, OrganizationMember
from app.models.user import User, UserStatus


class LegalWorkspaceCaseSharingTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(engine)
        self.db = self.session_factory()
        self.org1 = Organization(name="协作组织", code="share_org1")
        self.org2 = Organization(name="外部组织", code="share_org2")
        self.db.add_all([self.org1, self.org2])
        self.db.flush()

        def mk(username, org):
            u = User(username=username, email=f"{username}@example.com", hashed_password=hash_password("pw"), organization_id=org.id, status=UserStatus.active.value)
            self.db.add(u)
            return u

        self.member_a = mk("share-a", self.org1)
        self.member_b = mk("share-b", self.org1)
        self.outsider = mk("share-out", self.org2)
        self.db.flush()
        self.db.add_all([
            OrganizationMember(organization_id=self.org1.id, user_id=self.member_a.id, legal_role=LegalMemberRole.editor.value),
            OrganizationMember(organization_id=self.org1.id, user_id=self.member_b.id, legal_role=LegalMemberRole.editor.value),
            OrganizationMember(organization_id=self.org2.id, user_id=self.outsider.id, legal_role=LegalMemberRole.editor.value),
        ])
        self.case = LegalCase(title="共享案件", case_type="other", organization_id=self.org1.id, user_id=self.member_a.id)
        self.strict_case = LegalCase(title="严格案件", case_type="other", organization_id=self.org1.id, user_id=self.member_a.id, is_strict_mode=1)
        self.db.add_all([self.case, self.strict_case])
        self.db.flush()

        def consult(case_id):
            row = LegalConsultation(user_id=self.member_a.id, case_id=case_id, question="成员A的咨询", category="other", known_facts_json="[]", missing_facts_json="[]", references_json="[]", advice="建议", risk_level="medium", status="needs_lawyer_review")
            self.db.add(row)
            return row

        self.row = consult(self.case.id)
        self.strict_row = consult(self.strict_case.id)
        self.db.commit()
        self.db.refresh(self.row)
        self.db.refresh(self.strict_row)
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def _headers(self, user):
        return {"Authorization": f"Bearer {create_access_token({'sub': user.id})}"}

    def _detail(self, user, row):
        return self.client.get(f"/api/legal/consultations/{row.id}", headers=self._headers(user))

    def _list(self, user, case_id=None):
        url = "/api/legal/consultations" + (f"?case_id={case_id}" if case_id else "")
        return self.client.get(url, headers=self._headers(user))

    def test_owner_reads_own_case_record(self):
        self.assertEqual(self._detail(self.member_a, self.row).status_code, 200)

    def test_export_markdown_for_accessible_consultation(self):
        resp = self.client.get(
            f"/api/legal/consultations/{self.row.id}/export/markdown",
            headers=self._headers(self.member_b),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.headers["content-type"].startswith("text/markdown"))
        self.assertIn("legal_consultation_", resp.headers["content-disposition"])
        self.assertIn("# 法律咨询结果", resp.text)
        self.assertIn("成员A的咨询", resp.text)
        self.assertIn("建议", resp.text)

        listed = self.client.get(
            f"/api/legal/consultations/{self.row.id}/exports",
            headers=self._headers(self.member_b),
        )
        self.assertEqual(listed.status_code, 200, listed.text)
        records = listed.json()["data"]["items"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["format"], "markdown")
        self.assertNotIn("content", records[0])
        export_id = records[0]["id"]

        downloaded = self.client.get(
            f"/api/legal/consultations/exports/{export_id}/download",
            headers=self._headers(self.member_b),
        )
        self.assertEqual(downloaded.status_code, 200, downloaded.text)
        self.assertIn("成员A的咨询", downloaded.text)

        denied = self.client.get(
            f"/api/legal/consultations/exports/{export_id}/download",
            headers=self._headers(self.outsider),
        )
        self.assertEqual(denied.status_code, 404)

    def test_export_markdown_denied_for_outsider(self):
        resp = self.client.get(
            f"/api/legal/consultations/{self.row.id}/export/markdown",
            headers=self._headers(self.outsider),
        )
        self.assertEqual(resp.status_code, 404)

    def test_search_consultations_paginates_and_scopes_by_case(self):
        for index in range(3):
            self.db.add(LegalConsultation(
                user_id=self.member_a.id, case_id=self.case.id,
                question=f"分页问题 {index}", category="other",
                known_facts_json="[]", missing_facts_json="[]",
                references_json="[]", advice="分页建议", risk_level="low",
                status="pending_review",
            ))
        self.db.commit()

        first = self.client.get(
            f"/api/legal/consultations/search?q=分页&case_id={self.case.id}&limit=2&page=1",
            headers=self._headers(self.member_b),
        )
        self.assertEqual(first.status_code, 200, first.text)
        body = first.json()["data"]
        self.assertEqual(body["total"], 3)
        self.assertEqual(len(body["items"]), 2)
        self.assertTrue(body["has_more"])
        self.assertIsNotNone(body["next_cursor"])

        follow = self.client.get(
            f"/api/legal/consultations/search?q=分页&case_id={self.case.id}&limit=2&cursor={body['next_cursor']}",
            headers=self._headers(self.member_b),
        )
        self.assertEqual(follow.status_code, 200, follow.text)
        follow_body = follow.json()["data"]
        self.assertEqual(len(follow_body["items"]), 1)
        self.assertFalse(follow_body["has_more"])
        self.assertIsNone(follow_body["next_cursor"])

        second = self.client.get(
            f"/api/legal/consultations/search?q=分页&case_id={self.case.id}&limit=2&page=2",
            headers=self._headers(self.member_b),
        )
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(len(second.json()["data"]["items"]), 1)
        self.assertFalse(second.json()["data"]["has_more"])

        empty = self.client.get(
            f"/api/legal/consultations/search?q=不存在&case_id={self.case.id}",
            headers=self._headers(self.member_a),
        )
        self.assertEqual(empty.json()["data"]["total"], 0)

        denied = self.client.get(
            f"/api/legal/consultations/search?q=分页&case_id={self.case.id}",
            headers=self._headers(self.outsider),
        )
        self.assertIn(denied.status_code, (403, 404))

    def test_export_records_paginate(self):
        for _ in range(3):
            self.client.get(
                f"/api/legal/consultations/{self.row.id}/export/markdown",
                headers=self._headers(self.member_a),
            )
        page1 = self.client.get(
            f"/api/legal/consultations/{self.row.id}/exports?limit=2",
            headers=self._headers(self.member_a),
        ).json()["data"]
        self.assertEqual(len(page1["items"]), 2)
        self.assertTrue(page1["has_more"])
        self.assertIsNotNone(page1["next_cursor"])

        page2 = self.client.get(
            f"/api/legal/consultations/{self.row.id}/exports?limit=2&cursor={page1['next_cursor']}",
            headers=self._headers(self.member_a),
        ).json()["data"]
        self.assertEqual(len(page2["items"]), 1)
        self.assertFalse(page2["has_more"])
        self.assertIsNone(page2["next_cursor"])

    def test_export_records_prune_old_and_excess(self):
        now = utc_now()
        for index in range(25):
            self.db.add(LegalConsultationExport(
                consultation_id=self.row.id, user_id=self.member_a.id,
                format="markdown", filename=f"export-{index}.md", content="x",
                created_at=now - timedelta(minutes=index),
            ))
        self.db.add(LegalConsultationExport(
            consultation_id=self.row.id, user_id=self.member_a.id,
            format="markdown", filename="too-old.md", content="x",
            created_at=now - timedelta(days=120),
        ))
        self.db.commit()

        listed = self.client.get(
            f"/api/legal/consultations/{self.row.id}/exports?limit=50",
            headers=self._headers(self.member_a),
        ).json()["data"]
        self.assertEqual(len(listed["items"]), 20)
        self.assertEqual(listed["retention_days"], 90)
        self.assertEqual(listed["max_records"], 20)
        self.assertNotIn("too-old.md", [row["filename"] for row in listed["items"]])

    def test_case_member_reads_others_record(self):
        self.assertEqual(self._detail(self.member_b, self.row).status_code, 200)

    def test_outsider_denied(self):
        self.assertEqual(self._detail(self.outsider, self.row).status_code, 404)

    def test_case_scoped_list_returns_all_members(self):
        resp = self._list(self.member_b, self.case.id)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIn(self.row.id, [item["id"] for item in resp.json()["data"]])

    def test_personal_list_excludes_others(self):
        resp = self._list(self.member_b)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertNotIn(self.row.id, [item["id"] for item in resp.json()["data"]])

    def test_cross_org_case_list_denied(self):
        self.assertEqual(self._list(self.outsider, self.case.id).status_code, 404)

    def test_strict_case_blocks_non_member(self):
        self.assertEqual(self._detail(self.member_b, self.strict_row).status_code, 404)

    def test_strict_case_member_allowed(self):
        self.db.add(LegalCaseMember(case_id=self.strict_case.id, organization_id=self.org1.id, user_id=self.member_b.id, case_role="collaborator", granted_by=self.member_a.id))
        self.db.commit()
        self.assertEqual(self._detail(self.member_b, self.strict_row).status_code, 200)


if __name__ == "__main__":
    unittest.main()
