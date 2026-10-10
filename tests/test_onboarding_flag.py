"""首次引导完成标记（ux-audit P1-2 / 迁移 0095 users.onboarded_at）"""
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base, get_db
from app.main import app
from app.models.user import User


class OnboardingFlagTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        self.db = self.SessionLocal()

        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        self.db.close()
        app.dependency_overrides.clear()

    def _register(self, username="onboard_me"):
        resp = self.client.post("/api/auth/register", json={
            "username": username,
            "email": f"{username}@test.com",
            "password": "Str0ngPass!x",
            "full_name": "引导测试",
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()["data"]

    def test_register_leaves_onboarded_at_null(self):
        """新注册用户 onboarded_at 必须为 NULL（登录后进入引导）"""
        data = self._register("fresh_user")
        self.assertIsNone(data["user"]["onboarded_at"])

    def test_complete_onboarding_sets_flag_and_is_idempotent(self):
        data = self._register("completing_user")
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        first = self.client.post("/api/auth/complete-onboarding", headers=headers)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertTrue(first.json()["data"]["completed"])

        me = self.client.get("/api/auth/me", headers=headers)
        self.assertIsNotNone(me.json()["data"]["onboarded_at"])

        # 幂等：重复调用不报错、标记不变
        second = self.client.post("/api/auth/complete-onboarding", headers=headers)
        self.assertEqual(second.status_code, 200)

    def test_me_requires_auth(self):
        resp = self.client.post("/api/auth/complete-onboarding")
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
