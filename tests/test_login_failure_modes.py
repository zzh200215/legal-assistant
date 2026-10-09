"""登录失败三态拆分（ux-audit M-7）：密码错误 / 账号锁定 / 账号禁用 返回可区分的错误码与文案"""
import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import hash_password
from app.core.database import Base, get_db
from app.main import app
from app.models.user import User, UserStatus


class LoginFailureModeTests(unittest.TestCase):
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

        self.active_user = User(
            username="active_user", email="active@test.com",
            hashed_password=hash_password("RightPass123"), status=UserStatus.active.value,
        )
        locked_until = datetime.now(timezone.utc) + timedelta(minutes=15)
        self.locked_user = User(
            username="locked_user", email="locked@test.com",
            hashed_password=hash_password("RightPass123"), status=UserStatus.locked.value,
            locked_until=locked_until.replace(tzinfo=None),
        )
        self.disabled_user = User(
            username="disabled_user", email="disabled@test.com",
            hashed_password=hash_password("RightPass123"), status=UserStatus.disabled.value,
        )
        for user in (self.active_user, self.locked_user, self.disabled_user):
            self.db.add(user)
        self.db.commit()

        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        self.db.close()
        app.dependency_overrides.clear()

    def _login(self, username, password="RightPass123"):
        return self.client.post("/api/auth/login", json={"username": username, "password": password})

    def test_wrong_password_returns_invalid_credentials(self):
        resp = self._login("active_user", password="WrongPass")
        self.assertEqual(resp.status_code, 401)
        payload = resp.json()
        self.assertEqual(payload["error"]["code"], "INVALID_CREDENTIALS")
        self.assertEqual(payload["error"]["detail"], "用户名或密码错误")
        # 三态拆分后，密码错误不再混入"锁定/禁用"字样
        self.assertNotIn("锁定", payload["error"]["detail"])

    def test_unknown_user_indistinguishable_from_wrong_password(self):
        resp = self._login("no_such_user_xyz")
        self.assertEqual(resp.status_code, 401)
        payload = resp.json()
        self.assertEqual(payload["error"]["code"], "INVALID_CREDENTIALS")
        self.assertEqual(payload["error"]["detail"], "用户名或密码错误")

    def test_locked_account_returns_account_locked_with_remaining_minutes(self):
        resp = self._login("locked_user")
        self.assertEqual(resp.status_code, 401)
        payload = resp.json()
        self.assertEqual(payload["error"]["code"], "ACCOUNT_LOCKED")
        detail = payload["error"]["detail"]
        self.assertIn("临时锁定", detail)
        self.assertIn("分钟", detail)

    def test_disabled_account_returns_account_disabled(self):
        resp = self._login("disabled_user")
        self.assertEqual(resp.status_code, 403)
        payload = resp.json()
        self.assertEqual(payload["error"]["code"], "ACCOUNT_DISABLED")
        self.assertEqual(payload["error"]["detail"], "账号已被禁用，请联系管理员")


if __name__ == "__main__":
    unittest.main()
