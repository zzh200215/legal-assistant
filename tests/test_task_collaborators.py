"""任务协作者真实用户：同组织校验、权限、替换/清空与旧文本兼容。"""

import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import create_access_token, hash_password
from app.core.database import Base, get_db
from app.main import app
from app.models.org import LegalMemberRole, Organization, OrganizationMember
from app.models.user import User, UserStatus


class TaskCollaboratorTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        self.org = Organization(name="协作者组织", code="collab_org")
        self.other_org = Organization(name="协作者外部", code="collab_other")
        self.db.add_all([self.org, self.other_org])
        self.db.flush()

        def user(name, org):
            row = User(
                username=name, email=f"{name}@example.com",
                hashed_password=hash_password("pw"),
                organization_id=org.id, status=UserStatus.active.value,
            )
            self.db.add(row)
            return row

        self.owner = user("collab-owner", self.org)
        self.collaborator = user("collab-user", self.org)
        self.member = user("collab-member", self.org)
        self.outsider = user("collab-outsider", self.other_org)
        self.db.flush()
        self.db.add_all([
            OrganizationMember(organization_id=self.org.id, user_id=self.owner.id,
                               legal_role=LegalMemberRole.admin.value),
            OrganizationMember(organization_id=self.org.id, user_id=self.collaborator.id,
                               legal_role=LegalMemberRole.editor.value),
            OrganizationMember(organization_id=self.org.id, user_id=self.member.id,
                               legal_role=LegalMemberRole.editor.value),
            OrganizationMember(organization_id=self.other_org.id, user_id=self.outsider.id,
                               legal_role=LegalMemberRole.editor.value),
        ])
        self.db.commit()
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def _headers(self, user):
        return {"Authorization": f"Bearer {create_access_token({'sub': user.id})}"}

    def _create(self, *, collaborator_ids=None, collaborators=None, title="协作者任务"):
        payload = {"title": title}
        if collaborator_ids is not None:
            payload["collaborator_user_ids"] = collaborator_ids
        if collaborators is not None:
            payload["collaborators"] = collaborators
        response = self.client.post(
            "/api/tasks/", json=payload, headers=self._headers(self.owner)
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]

    def test_create_with_collaborator_users(self):
        task = self._create(collaborator_ids=[self.collaborator.id])
        self.assertEqual(task["collaborator_user_ids"], [self.collaborator.id])
        self.assertEqual(task["collaborators"], ["collab-user"])

    def test_cross_organization_collaborator_rejected(self):
        response = self.client.post(
            "/api/tasks/",
            json={"title": "跨组织协作者", "collaborator_user_ids": [self.outsider.id]},
            headers=self._headers(self.owner),
        )
        self.assertEqual(response.status_code, 400, response.text)

    def test_collaborator_can_update_status_progress_and_comment(self):
        task = self._create(collaborator_ids=[self.collaborator.id])
        updated = self.client.put(
            f"/api/tasks/{task['id']}",
            json={"status": "in_progress", "progress": 30},
            headers=self._headers(self.collaborator),
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        commented = self.client.post(
            f"/api/tasks/{task['id']}/comments",
            json={"content": "协作者补充"},
            headers=self._headers(self.collaborator),
        )
        self.assertEqual(commented.status_code, 200, commented.text)

    def test_collaborator_cannot_edit_title_or_reassign(self):
        task = self._create(collaborator_ids=[self.collaborator.id])
        denied = self.client.put(
            f"/api/tasks/{task['id']}",
            json={"title": "改标题"},
            headers=self._headers(self.collaborator),
        )
        self.assertEqual(denied.status_code, 403, denied.text)
        reassign = self.client.put(
            f"/api/tasks/{task['id']}",
            json={"assignee_user_id": self.member.id},
            headers=self._headers(self.collaborator),
        )
        self.assertEqual(reassign.status_code, 403, reassign.text)

    def test_unrelated_member_remains_read_only(self):
        task = self._create(collaborator_ids=[self.collaborator.id])
        denied = self.client.put(
            f"/api/tasks/{task['id']}",
            json={"status": "done"},
            headers=self._headers(self.member),
        )
        self.assertEqual(denied.status_code, 403, denied.text)

    def test_owner_replaces_and_clears_collaborators(self):
        task = self._create(collaborator_ids=[self.collaborator.id])
        replaced = self.client.put(
            f"/api/tasks/{task['id']}",
            json={"collaborator_user_ids": [self.member.id]},
            headers=self._headers(self.owner),
        )
        self.assertEqual(replaced.status_code, 200, replaced.text)
        self.assertEqual(replaced.json()["data"]["collaborator_user_ids"], [self.member.id])
        self.assertEqual(replaced.json()["data"]["collaborators"], ["collab-member"])

        cleared = self.client.put(
            f"/api/tasks/{task['id']}",
            json={"collaborator_user_ids": []},
            headers=self._headers(self.owner),
        )
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertEqual(cleared.json()["data"]["collaborator_user_ids"], [])
        self.assertEqual(cleared.json()["data"]["collaborators"], [])

    def test_legacy_text_collaborators_remain_compatible(self):
        task = self._create(collaborators=["张三", "李四"])
        self.assertEqual(task["collaborator_user_ids"], [])
        self.assertEqual(task["collaborators"], ["张三", "李四"])

    def test_participating_scope_includes_collaborator(self):
        task = self._create(collaborator_ids=[self.collaborator.id])
        participating = self.client.get(
            "/api/tasks/?scope=participating", headers=self._headers(self.collaborator)
        )
        self.assertEqual(participating.status_code, 200, participating.text)
        self.assertEqual(
            [item["id"] for item in participating.json()["data"]["items"]], [task["id"]]
        )

        mine = self.client.get(
            "/api/tasks/?scope=mine", headers=self._headers(self.collaborator)
        )
        self.assertEqual(mine.status_code, 200, mine.text)
        self.assertEqual(mine.json()["data"]["items"], [])

        shared = self.client.get(
            "/api/tasks/?scope=shared", headers=self._headers(self.collaborator)
        )
        self.assertEqual(shared.status_code, 200, shared.text)
        self.assertEqual(shared.json()["data"]["items"], [])
