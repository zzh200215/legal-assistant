"""任务批量改派：owner-only、跳过无权限、跨组织拒绝、清空负责人。"""

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
from app.models.task import Task, TaskLog
from app.models.user import User, UserStatus


class TaskBulkReassignTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        self.org = Organization(name="批量组织", code="bulk_org")
        self.other_org = Organization(name="批量外部", code="bulk_other")
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

        self.owner = user("bulk-owner", self.org)
        self.assignee = user("bulk-assignee", self.org)
        self.member = user("bulk-member", self.org)
        self.outsider = user("bulk-outsider", self.other_org)
        self.db.flush()
        self.db.add_all([
            OrganizationMember(organization_id=self.org.id, user_id=self.owner.id,
                               legal_role=LegalMemberRole.admin.value),
            OrganizationMember(organization_id=self.org.id, user_id=self.assignee.id,
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

    def _task(self, owner, title):
        task = Task(
            user_id=owner.id,
            organization_id=owner.organization_id,
            title=title,
            status="todo",
            priority="medium",
            progress=0,
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def test_owner_bulk_reassign_updates_owned_and_skips_others(self):
        first = self._task(self.owner, "批量一")
        second = self._task(self.owner, "批量二")
        others = self._task(self.member, "别人的任务")

        response = self.client.post(
            "/api/tasks/bulk-reassign",
            json={
                "task_ids": [first.id, second.id, others.id, first.id],
                "assignee_user_id": self.assignee.id,
            },
            headers=self._headers(self.owner),
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(data["updated"], 2)
        self.assertEqual(data["skipped"], 1)
        self.assertEqual(data["assignee_user_id"], self.assignee.id)

        for task in (first, second):
            self.db.refresh(task)
            self.assertEqual(task.assignee_user_id, self.assignee.id)
            self.assertEqual(task.assignee, "bulk-assignee")
        self.db.refresh(others)
        self.assertIsNone(others.assignee_user_id)
        logs = self.db.query(TaskLog).filter(
            TaskLog.task_id.in_([first.id, second.id]),
            TaskLog.action == "assignee_changed",
        ).all()
        self.assertEqual(len(logs), 2)

    def test_bulk_reassign_rejects_cross_organization_assignee(self):
        task = self._task(self.owner, "跨组织")
        response = self.client.post(
            "/api/tasks/bulk-reassign",
            json={"task_ids": [task.id], "assignee_user_id": self.outsider.id},
            headers=self._headers(self.owner),
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["error"]["code"], "TASK_ASSIGNEE_INVALID")
        self.db.refresh(task)
        self.assertIsNone(task.assignee_user_id)

    def test_bulk_reassign_can_clear_and_validates_input(self):
        first = self._task(self.owner, "清空一")
        second = self._task(self.owner, "清空二")
        for task in (first, second):
            task.assignee_user_id = self.assignee.id
            task.assignee = "bulk-assignee"
        self.db.commit()

        response = self.client.post(
            "/api/tasks/bulk-reassign",
            json={"task_ids": [first.id, second.id], "assignee_user_id": None},
            headers=self._headers(self.owner),
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["updated"], 2)
        for task in (first, second):
            self.db.refresh(task)
            self.assertIsNone(task.assignee_user_id)
            self.assertIsNone(task.assignee)

        empty = self.client.post(
            "/api/tasks/bulk-reassign",
            json={"task_ids": [], "assignee_user_id": None},
            headers=self._headers(self.owner),
        )
        self.assertEqual(empty.status_code, 422, empty.text)
