"""任务日志/评论操作人：新日志写 user_id，读接口批量补 username/full_name。"""

import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import create_access_token, hash_password
from app.core.database import Base, get_db
from app.main import app
from app.models.org import Organization
from app.models.task import Task, TaskLog
from app.models.user import User, UserStatus


class TaskAuditNamesTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        self.org = Organization(name="审计组织", code="audit_org")
        self.db.add(self.org)
        self.db.flush()
        self.user = User(
            username="audit-owner", full_name="主办律师",
            email="audit-owner@example.com", hashed_password=hash_password("pw"),
            organization_id=self.org.id, status=UserStatus.active.value,
        )
        self.db.add(self.user)
        self.db.commit()
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def _headers(self):
        return {"Authorization": f"Bearer {create_access_token({'sub': self.user.id})}"}

    def test_logs_and_comments_expose_operator_name(self):
        created = self.client.post(
            "/api/tasks/", json={"title": "审计任务"}, headers=self._headers()
        )
        self.assertEqual(created.status_code, 200, created.text)
        task_id = created.json()["data"]["id"]
        self.client.put(
            f"/api/tasks/{task_id}", json={"status": "in_progress"}, headers=self._headers()
        )
        self.client.post(
            f"/api/tasks/{task_id}/comments", json={"content": "进展正常"}, headers=self._headers()
        )

        logs = self.client.get(f"/api/tasks/{task_id}/logs", headers=self._headers())
        self.assertEqual(logs.status_code, 200, logs.text)
        created_log = next(item for item in logs.json()["data"] if item["action"] == "created")
        self.assertEqual(created_log["user_id"], self.user.id)
        self.assertEqual(created_log["username"], "audit-owner")
        self.assertEqual(created_log["full_name"], "主办律师")

        comments = self.client.get(f"/api/tasks/{task_id}/comments", headers=self._headers())
        self.assertEqual(comments.status_code, 200, comments.text)
        self.assertEqual(comments.json()["data"][0]["username"], "audit-owner")
        self.assertEqual(comments.json()["data"][0]["full_name"], "主办律师")

    def test_legacy_log_without_user_has_no_name(self):
        task = Task(
            user_id=self.user.id, organization_id=self.org.id,
            title="历史任务", status="todo", priority="medium", progress=0,
        )
        self.db.add(task)
        self.db.flush()
        self.db.add(TaskLog(task_id=task.id, action="created", detail="legacy"))
        self.db.commit()

        logs = self.client.get(f"/api/tasks/{task.id}/logs", headers=self._headers())
        self.assertEqual(logs.status_code, 200, logs.text)
        row = logs.json()["data"][0]
        self.assertIsNone(row["user_id"])
        self.assertIsNone(row["username"])
        self.assertIsNone(row["full_name"])
