"""#95/账号注销冷却期 API 回归测试"""
import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import hash_password, create_access_token
from app.core.database import Base, get_db
from app.main import app
from app.models.user import User, UserStatus


def _make_engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


class AccountDeletionApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session()

        self.user = User(
            username="del_user", email="del@t.com",
            hashed_password=hash_password("pw"), role="user",
            status=UserStatus.active.value, full_name="张三",
        )
        self.admin = User(
            username="del_admin", email="da@t.com",
            hashed_password=hash_password("pw"), role="admin",
            status=UserStatus.active.value,
        )
        self.db.add_all([self.user, self.admin])
        self.db.commit()

        self.token = create_access_token({"sub": str(self.user.id)})
        self.admin_token = create_access_token({"sub": str(self.admin.id)})

        def _override_db():
            db = Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = _override_db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.pop(get_db, None)
        self.db.close()
        self.engine.dispose()

    def _headers(self, token):
        return {"Authorization": f"Bearer {token}"}

    def test_request_cool_down(self):
        r = self.client.post("/api/auth/account-deletion/request", headers=self._headers(self.token))
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()["data"]
        self.assertEqual(data["status"], "deletion_pending")
        self.assertEqual(data["cool_down_days"], 30)
        self.db.refresh(self.user)
        self.assertEqual(self.user.status, UserStatus.deletion_pending.value)

    def test_cancel_deletion(self):
        self.client.post("/api/auth/account-deletion/request", headers=self._headers(self.token))
        r = self.client.post("/api/auth/account-deletion/cancel", headers=self._headers(self.token))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["data"]["status"], "active")
        self.db.refresh(self.user)
        self.assertIsNone(self.user.deletion_requested_at)

    def test_confirm_blocked_in_cool_down(self):
        self.client.post("/api/auth/account-deletion/request", headers=self._headers(self.token))
        r = self.client.post("/api/auth/account-deletion/confirm", headers=self._headers(self.token))
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("冷却期", r.text)

    def test_admin_force_confirm_anonymizes(self):
        self.client.post("/api/auth/account-deletion/request", headers=self._headers(self.token))
        r = self.client.post(
            f"/api/auth/admin/account-deletions/{self.user.id}/confirm",
            headers=self._headers(self.admin_token),
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.db.refresh(self.user)
        self.assertEqual(self.user.status, UserStatus.deleted.value)
        self.assertTrue(self.user.username.startswith("deleted_"))
        self.assertTrue(self.user.email.endswith("@deleted.local"))
        self.assertIsNone(self.user.full_name)
        self.assertIsNone(self.user.hashed_password)

    def test_admin_list_pending(self):
        self.client.post("/api/auth/account-deletion/request", headers=self._headers(self.token))
        r = self.client.get("/api/auth/admin/account-deletions", headers=self._headers(self.admin_token))
        self.assertEqual(r.status_code, 200, r.text)
        items = r.json()["data"]["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["remaining_days"], 30)

    def test_admin_endpoints_require_admin(self):
        r = self.client.get("/api/auth/admin/account-deletions", headers=self._headers(self.token))
        self.assertEqual(r.status_code, 403, r.text)

    def test_service_confirms_expired_pending(self):
        """验证 confirm_expired_pending service 能自动确认冷却期已满的注销请求（beat 任务实际调用的是这个）"""
        from app.services.auth.account_deletion_service import request_deletion, confirm_expired_pending, DELETION_COOL_DOWN_DAYS

        # 发起注销并手动回拨 requested_at 到 31 天前
        request_deletion(self.db, self.user)
        self.user.deletion_requested_at = datetime.now(timezone.utc) - timedelta(days=DELETION_COOL_DOWN_DAYS + 1)
        self.db.commit()

        # 直接调用 service（beat task 调用的就是这个）
        confirmed_count = confirm_expired_pending(self.db)

        self.assertEqual(confirmed_count, 1)
        self.db.refresh(self.user)
        self.assertEqual(self.user.status, UserStatus.deleted.value)
        self.assertTrue(self.user.username.startswith("deleted_"))


if __name__ == "__main__":
    unittest.main()
