"""任务优先级筛选：priority 下推 SQL，可与 status 组合。"""

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
from app.models.task import Task
from app.models.user import User, UserStatus


class TaskPriorityFilterTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        self.org = Organization(name="优先级组织", code="priority_org")
        self.db.add(self.org)
        self.db.flush()
        self.user = User(
            username="priority-owner", email="priority-owner@example.com",
            hashed_password=hash_password("pw"),
            organization_id=self.org.id, status=UserStatus.active.value,
        )
        self.db.add(self.user)
        self.db.commit()
        self.db.add_all([
            Task(user_id=self.user.id, organization_id=self.org.id, title="高优先级",
                 status="todo", priority="high", progress=0),
            Task(user_id=self.user.id, organization_id=self.org.id, title="中优先级",
                 status="in_progress", priority="medium", progress=30),
            Task(user_id=self.user.id, organization_id=self.org.id, title="低优先级",
                 status="done", priority="low", progress=100),
        ])
        self.db.commit()
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def _headers(self):
        return {"Authorization": f"Bearer {create_access_token({'sub': self.user.id})}"}

    def test_priority_filter(self):
        response = self.client.get("/api/tasks/?priority=high", headers=self._headers())
        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()["data"]["items"]
        self.assertEqual([item["title"] for item in items], ["高优先级"])

    def test_priority_combines_with_status(self):
        response = self.client.get(
            "/api/tasks/?priority=medium&status=in_progress", headers=self._headers()
        )
        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()["data"]["items"]
        self.assertEqual([item["title"] for item in items], ["中优先级"])

    def test_invalid_priority(self):
        response = self.client.get("/api/tasks/?priority=urgent", headers=self._headers())
        self.assertEqual(response.status_code, 422, response.text)
