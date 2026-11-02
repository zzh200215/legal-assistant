"""任务评论编辑/删除：作者可改自己的，owner 可删任意；越权与空内容拒绝。"""

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
from app.models.task import Task
from app.models.user import User, UserStatus


class TaskCommentEditTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        self.org = Organization(name="评论组织", code="comment_org")
        self.db.add(self.org)
        self.db.flush()

        def user(name):
            row = User(
                username=name, full_name=name,
                email=f"{name}@example.com", hashed_password=hash_password("pw"),
                organization_id=self.org.id, status=UserStatus.active.value,
            )
            self.db.add(row)
            return row

        self.owner = user("comment-owner")
        self.member = user("comment-member")
        self.db.flush()
        self.db.add_all([
            OrganizationMember(organization_id=self.org.id, user_id=self.owner.id,
                               legal_role=LegalMemberRole.admin.value),
            OrganizationMember(organization_id=self.org.id, user_id=self.member.id,
                               legal_role=LegalMemberRole.editor.value),
        ])
        self.task = Task(
            user_id=self.owner.id, organization_id=self.org.id,
            title="评论任务", status="todo", priority="medium", progress=0,
            collaborators="[]",
        )
        self.db.add(self.task)
        self.db.commit()
        self.db.refresh(self.task)
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def _headers(self, user):
        return {"Authorization": f"Bearer {create_access_token({'sub': user.id})}"}

    def _comment(self, user, content="原始评论"):
        response = self.client.post(
            f"/api/tasks/{self.task.id}/comments",
            json={"content": content},
            headers=self._headers(user),
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]

    def test_author_edits_own_comment(self):
        comment = self._comment(self.owner)
        response = self.client.patch(
            f"/api/tasks/{self.task.id}/comments/{comment['id']}",
            json={"content": "更新后的评论"},
            headers=self._headers(self.owner),
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["content"], "更新后的评论")

    def test_other_member_cannot_edit_or_delete(self):
        comment = self._comment(self.owner)
        edit = self.client.patch(
            f"/api/tasks/{self.task.id}/comments/{comment['id']}",
            json={"content": "越权修改"},
            headers=self._headers(self.member),
        )
        self.assertEqual(edit.status_code, 403, edit.text)
        delete = self.client.delete(
            f"/api/tasks/{self.task.id}/comments/{comment['id']}",
            headers=self._headers(self.member),
        )
        self.assertEqual(delete.status_code, 403, delete.text)

    def test_owner_deletes_any_comment(self):
        comment = self._comment(self.owner)
        response = self.client.delete(
            f"/api/tasks/{self.task.id}/comments/{comment['id']}",
            headers=self._headers(self.owner),
        )
        self.assertEqual(response.status_code, 204, response.text)
        listed = self.client.get(
            f"/api/tasks/{self.task.id}/comments", headers=self._headers(self.owner)
        )
        self.assertEqual(listed.json()["data"], [])

    def test_author_deletes_own_comment(self):
        comment = self._comment(self.owner)
        response = self.client.delete(
            f"/api/tasks/{self.task.id}/comments/{comment['id']}",
            headers=self._headers(self.owner),
        )
        self.assertEqual(response.status_code, 204, response.text)

    def test_empty_content_and_wrong_comment_id(self):
        comment = self._comment(self.owner)
        empty = self.client.patch(
            f"/api/tasks/{self.task.id}/comments/{comment['id']}",
            json={"content": "   "},
            headers=self._headers(self.owner),
        )
        self.assertEqual(empty.status_code, 400, empty.text)

        missing = self.client.patch(
            f"/api/tasks/{self.task.id}/comments/999999",
            json={"content": "不存在"},
            headers=self._headers(self.owner),
        )
        self.assertEqual(missing.status_code, 404, missing.text)
