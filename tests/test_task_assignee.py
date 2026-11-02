"""任务负责人真实用户：同组织校验、负责人协作权限、创建/改派/清空。"""

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


class TaskAssigneeTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        self.org = Organization(name="负责人组织", code="assignee_org")
        self.other_org = Organization(name="外部组织", code="assignee_other")
        self.db.add_all([self.org, self.other_org])
        self.db.flush()

        def user(name, org):
            row = User(
                username=name,
                email=f"{name}@example.com",
                hashed_password=hash_password("pw"),
                organization_id=org.id,
                status=UserStatus.active.value,
            )
            self.db.add(row)
            return row

        self.owner = user("assignee-owner", self.org)
        self.assignee = user("assignee-user", self.org)
        self.member = user("assignee-member", self.org)
        self.outsider = user("assignee-outsider", self.other_org)
        self.db.flush()
        self.db.add_all([
            OrganizationMember(
                organization_id=self.org.id, user_id=self.owner.id,
                legal_role=LegalMemberRole.admin.value,
            ),
            OrganizationMember(
                organization_id=self.org.id, user_id=self.assignee.id,
                legal_role=LegalMemberRole.editor.value,
            ),
            OrganizationMember(
                organization_id=self.org.id, user_id=self.member.id,
                legal_role=LegalMemberRole.editor.value,
            ),
            OrganizationMember(
                organization_id=self.other_org.id, user_id=self.outsider.id,
                legal_role=LegalMemberRole.editor.value,
            ),
        ])
        self.db.commit()
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def _headers(self, user):
        return {"Authorization": f"Bearer {create_access_token({'sub': user.id})}"}

    def _create(self, assignee_id=None, title="负责人任务"):
        payload = {"title": title}
        if assignee_id is not None:
            payload["assignee_user_id"] = assignee_id
        response = self.client.post(
            "/api/tasks/", json=payload, headers=self._headers(self.owner)
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]

    def test_create_with_assignee_sets_user_and_display_name(self):
        task = self._create(self.assignee.id)

        self.assertEqual(task["assignee_user_id"], self.assignee.id)
        self.assertEqual(task["assignee"], "assignee-user")

    def test_cross_organization_assignee_rejected(self):
        response = self.client.post(
            "/api/tasks/",
            json={"title": "跨组织", "assignee_user_id": self.outsider.id},
            headers=self._headers(self.owner),
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["error"]["code"], "TASK_ASSIGNEE_INVALID")

    def test_assignee_can_update_status_progress_and_comment(self):
        task = self._create(self.assignee.id)

        updated = self.client.put(
            f"/api/tasks/{task['id']}",
            json={"status": "in_progress", "progress": 40},
            headers=self._headers(self.assignee),
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["data"]["status"], "in_progress")
        self.assertEqual(updated.json()["data"]["progress"], 40)

        commented = self.client.post(
            f"/api/tasks/{task['id']}/comments",
            json={"content": "负责人补充进展"},
            headers=self._headers(self.assignee),
        )
        self.assertEqual(commented.status_code, 200, commented.text)

    def test_assignee_cannot_edit_title_or_reassign(self):
        task = self._create(self.assignee.id)

        title_update = self.client.put(
            f"/api/tasks/{task['id']}",
            json={"title": "改标题"},
            headers=self._headers(self.assignee),
        )
        self.assertEqual(title_update.status_code, 403, title_update.text)
        self.assertEqual(title_update.json()["error"]["code"], "TASK_EDIT_FORBIDDEN")

        reassign = self.client.put(
            f"/api/tasks/{task['id']}",
            json={"assignee_user_id": self.member.id},
            headers=self._headers(self.assignee),
        )
        self.assertEqual(reassign.status_code, 403, reassign.text)

    def test_shared_member_without_assignment_is_read_only(self):
        task = self._create(self.assignee.id)

        denied = self.client.put(
            f"/api/tasks/{task['id']}",
            json={"status": "done"},
            headers=self._headers(self.member),
        )
        self.assertEqual(denied.status_code, 403, denied.text)
        denied_comment = self.client.post(
            f"/api/tasks/{task['id']}/comments",
            json={"content": "无关成员"},
            headers=self._headers(self.member),
        )
        self.assertEqual(denied_comment.status_code, 403, denied_comment.text)

    def test_assignee_sees_task_in_mine_but_not_shared(self):
        task = self._create(self.assignee.id)

        def listing(user, scope):
            response = self.client.get(
                "/api/tasks/",
                params={"scope": scope, "page": 1, "page_size": 50},
                headers=self._headers(user),
            )
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()["data"]

        mine = listing(self.assignee, "mine")
        self.assertEqual(mine["total"], 1)
        self.assertEqual(mine["items"][0]["id"], task["id"])
        self.assertEqual(mine["stats"]["shared"], 0)

        shared = listing(self.assignee, "shared")
        self.assertEqual(shared["total"], 0)

        member = listing(self.member, "all")
        self.assertEqual(member["total"], 1)
        self.assertEqual(member["stats"]["shared"], 1)

    def test_owner_can_reassign_and_clear(self):
        task = self._create(self.assignee.id)

        reassigned = self.client.put(
            f"/api/tasks/{task['id']}",
            json={"assignee_user_id": self.member.id},
            headers=self._headers(self.owner),
        )
        self.assertEqual(reassigned.status_code, 200, reassigned.text)
        self.assertEqual(reassigned.json()["data"]["assignee_user_id"], self.member.id)
        self.assertEqual(reassigned.json()["data"]["assignee"], "assignee-member")

        cleared = self.client.put(
            f"/api/tasks/{task['id']}",
            json={"assignee_user_id": None},
            headers=self._headers(self.owner),
        )
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertIsNone(cleared.json()["data"]["assignee_user_id"])
        self.assertIsNone(cleared.json()["data"]["assignee"])
