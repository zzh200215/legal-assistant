"""任务批量操作：完成、归档、改优先级；权限与归档任务跳过。"""

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


class TaskBulkActionTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        self.org = Organization(name="批量操作组织", code="bulk_action_org")
        self.db.add(self.org)
        self.db.flush()

        def user(name):
            row = User(
                username=name, email=f"{name}@example.com",
                hashed_password=hash_password("pw"),
                organization_id=self.org.id, status=UserStatus.active.value,
            )
            self.db.add(row)
            return row

        self.owner = user("action-owner")
        self.assignee = user("action-assignee")
        self.member = user("action-member")
        self.db.flush()
        self.db.add_all([
            OrganizationMember(organization_id=self.org.id, user_id=self.owner.id,
                               legal_role=LegalMemberRole.admin.value),
            OrganizationMember(organization_id=self.org.id, user_id=self.assignee.id,
                               legal_role=LegalMemberRole.editor.value),
            OrganizationMember(organization_id=self.org.id, user_id=self.member.id,
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

    def _task(self, *, owner=None, assignee=None, title="批量任务", priority="medium"):
        task = Task(
            user_id=(owner or self.owner).id,
            organization_id=self.org.id,
            title=title,
            status="todo",
            priority=priority,
            progress=0,
            assignee_user_id=assignee.id if assignee else None,
            assignee=assignee.username if assignee else None,
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def _post(self, user, **payload):
        return self.client.post(
            "/api/tasks/bulk-action", json=payload, headers=self._headers(user)
        )

    def test_complete_as_owner_and_assignee_skips_others(self):
        owned = self._task(title="自己的任务")
        assigned = self._task(title="指派任务", assignee=self.assignee)
        others = self._task(title="别人的任务", owner=self.member)

        response = self._post(
            self.owner,
            task_ids=[owned.id, assigned.id, others.id],
            action="complete",
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["updated"], 2)
        self.assertEqual(response.json()["data"]["skipped"], 1)
        self.db.refresh(owned)
        self.assertEqual(owned.status, "done")
        self.assertEqual(owned.progress, 100)

        # 负责人可以完成指派给自己的任务
        response = self._post(
            self.assignee, task_ids=[assigned.id], action="complete"
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["updated"], 1)
        self.db.refresh(assigned)
        self.assertEqual(assigned.status, "done")

    def test_archive_and_priority_are_owner_only(self):
        task = self._task(title="归档任务")
        denied = self._post(self.member, task_ids=[task.id], action="archive")
        self.assertEqual(denied.status_code, 200, denied.text)
        self.assertEqual(denied.json()["data"]["updated"], 0)
        self.assertEqual(denied.json()["data"]["skipped"], 1)
        self.db.refresh(task)
        self.assertIsNone(task.archived_at)

        archived = self._post(self.owner, task_ids=[task.id], action="archive")
        self.assertEqual(archived.status_code, 200, archived.text)
        self.assertEqual(archived.json()["data"]["updated"], 1)
        self.db.refresh(task)
        self.assertIsNotNone(task.archived_at)
        self.assertTrue(
            self.db.query(TaskLog).filter(
                TaskLog.task_id == task.id, TaskLog.action == "archived"
            ).first()
        )

    def test_priority_update_and_archived_skip(self):
        task = self._task(title="优先级任务", priority="medium")
        response = self._post(
            self.owner, task_ids=[task.id], action="priority", priority="high"
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["updated"], 1)
        self.db.refresh(task)
        self.assertEqual(task.priority, "high")
        self.assertTrue(
            self.db.query(TaskLog).filter(
                TaskLog.task_id == task.id, TaskLog.action == "priority_changed"
            ).first()
        )

        task.archived_at = task.created_at
        self.db.commit()
        skipped = self._post(
            self.owner, task_ids=[task.id], action="complete"
        )
        self.assertEqual(skipped.status_code, 200, skipped.text)
        self.assertEqual(skipped.json()["data"]["updated"], 0)
        self.assertEqual(skipped.json()["data"]["skipped"], 1)

    def test_invalid_payloads(self):
        task = self._task(title="非法操作")
        missing_priority = self._post(
            self.owner, task_ids=[task.id], action="priority"
        )
        self.assertEqual(missing_priority.status_code, 400, missing_priority.text)

        invalid_priority = self._post(
            self.owner, task_ids=[task.id], action="priority", priority="urgent"
        )
        self.assertEqual(invalid_priority.status_code, 422, invalid_priority.text)

        empty = self._post(self.owner, task_ids=[], action="complete")
        self.assertEqual(empty.status_code, 422, empty.text)
