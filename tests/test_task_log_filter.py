"""任务日志筛选/分页：按动作、limit/offset、asc/desc。"""

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
from app.models.user import User, UserStatus


class TaskLogFilterTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        self.org = Organization(name="日志组织", code="log_org")
        self.db.add(self.org)
        self.db.flush()
        self.user = User(
            username="log-owner", email="log-owner@example.com",
            hashed_password=hash_password("pw"),
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

    def _seed(self):
        created = self.client.post(
            "/api/tasks/", json={"title": "日志任务"}, headers=self._headers()
        )
        self.assertEqual(created.status_code, 200, created.text)
        task_id = created.json()["data"]["id"]
        self.client.put(
            f"/api/tasks/{task_id}", json={"status": "in_progress"}, headers=self._headers()
        )
        self.client.post(
            f"/api/tasks/{task_id}/comments", json={"content": "一条评论"}, headers=self._headers()
        )
        return task_id

    def test_filter_by_action(self):
        task_id = self._seed()
        response = self.client.get(
            f"/api/tasks/{task_id}/logs?action=comment_added", headers=self._headers()
        )
        self.assertEqual(response.status_code, 200, response.text)
        rows = response.json()["data"]
        self.assertEqual([row["action"] for row in rows], ["comment_added"])

    def test_limit_offset_and_order(self):
        task_id = self._seed()
        latest = self.client.get(
            f"/api/tasks/{task_id}/logs?limit=1&order=desc", headers=self._headers()
        )
        self.assertEqual(latest.status_code, 200, latest.text)
        self.assertEqual(latest.json()["data"][0]["action"], "comment_added")

        oldest = self.client.get(
            f"/api/tasks/{task_id}/logs?limit=1&order=asc", headers=self._headers()
        )
        self.assertEqual(oldest.json()["data"][0]["action"], "created")

        second = self.client.get(
            f"/api/tasks/{task_id}/logs?limit=1&offset=1&order=asc", headers=self._headers()
        )
        self.assertEqual(second.json()["data"][0]["action"], "status_changed")

    def test_invalid_pagination(self):
        task_id = self._seed()
        too_many = self.client.get(
            f"/api/tasks/{task_id}/logs?limit=101", headers=self._headers()
        )
        self.assertEqual(too_many.status_code, 422, too_many.text)
