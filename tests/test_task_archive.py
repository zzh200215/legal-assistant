"""任务归档：owner 可归档/恢复；默认列表、统计、提醒都排除归档任务。"""

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
from app.models.org import LegalMemberRole, Organization, OrganizationMember
from app.models.task import Task, TaskLog
from app.models.user import User, UserStatus


class TaskArchiveTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        self.org = Organization(name="归档组织", code="archive_org")
        self.db.add(self.org)
        self.db.flush()

        def user(name, org_id):
            row = User(
                username=name,
                email=f"{name}@example.com",
                hashed_password=hash_password("pw"),
                organization_id=org_id,
                status=UserStatus.active.value,
            )
            self.db.add(row)
            return row

        self.owner = user("archive-owner", self.org.id)
        self.member = user("archive-member", self.org.id)
        self.outsider = user("archive-outsider", None)
        self.db.flush()
        self.db.add_all([
            OrganizationMember(
                organization_id=self.org.id, user_id=self.owner.id,
                legal_role=LegalMemberRole.editor.value,
            ),
            OrganizationMember(
                organization_id=self.org.id, user_id=self.member.id,
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

    def _task(self, *, title="待归档任务"):
        task = Task(
            user_id=self.owner.id,
            organization_id=self.org.id,
            title=title,
            status="todo",
            priority="medium",
            progress=0,
            due_date=utc_now() + timedelta(days=1),
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def _list(self, user, **params):
        response = self.client.get(
            "/api/tasks/", params={"page": 1, "page_size": 50, **params},
            headers=self._headers(user),
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]

    def test_owner_archive_unarchive_and_list_stats(self):
        task = self._task(title="归档我")

        archived = self.client.post(
            f"/api/tasks/{task.id}/archive", headers=self._headers(self.owner)
        )
        self.assertEqual(archived.status_code, 200, archived.text)
        self.assertIsNotNone(archived.json()["data"]["archived_at"])

        active = self._list(self.owner)
        self.assertEqual(active["total"], 0)
        self.assertEqual(active["stats"]["total"], 0)
        archived_list = self._list(self.owner, archived="true")
        self.assertEqual(archived_list["total"], 1)
        self.assertEqual(archived_list["stats"]["total"], 1)
        self.assertEqual(archived_list["items"][0]["id"], task.id)

        detail = self.client.get(
            f"/api/tasks/{task.id}", headers=self._headers(self.owner)
        )
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertIsNotNone(detail.json()["data"]["archived_at"])

        restored = self.client.post(
            f"/api/tasks/{task.id}/unarchive", headers=self._headers(self.owner)
        )
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertIsNone(restored.json()["data"]["archived_at"])
        self.assertEqual(self._list(self.owner)["total"], 1)
        self.assertEqual(self._list(self.owner, archived="true")["total"], 0)

    def test_non_owner_cannot_archive_or_unarchive(self):
        task = self._task(title="共享任务")
        denied = self.client.post(
            f"/api/tasks/{task.id}/archive", headers=self._headers(self.member)
        )
        self.assertEqual(denied.status_code, 404, denied.text)
        self.db.refresh(task)
        self.assertIsNone(task.archived_at)

        self.client.post(f"/api/tasks/{task.id}/archive", headers=self._headers(self.owner))
        denied_restore = self.client.post(
            f"/api/tasks/{task.id}/unarchive", headers=self._headers(self.member)
        )
        self.assertEqual(denied_restore.status_code, 404, denied_restore.text)

    def test_archive_is_idempotent_and_writes_single_log(self):
        task = self._task(title="重复归档")
        for _ in range(2):
            response = self.client.post(
                f"/api/tasks/{task.id}/archive", headers=self._headers(self.owner)
            )
            self.assertEqual(response.status_code, 200, response.text)
        for _ in range(2):
            response = self.client.post(
                f"/api/tasks/{task.id}/unarchive", headers=self._headers(self.owner)
            )
            self.assertEqual(response.status_code, 200, response.text)
        actions = [
            log.action for log in self.db.query(TaskLog)
            .filter(TaskLog.task_id == task.id).all()
        ]
        self.assertEqual(actions.count("archived"), 1)
        self.assertEqual(actions.count("unarchived"), 1)
