"""请假接口：默认空、设置与回显、状态、日期校验、同组织校验、结束请假。"""

import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import create_access_token, hash_password
from app.core.database import Base, get_db
from app.main import app
from app.models.legal_notifications import LegalNotificationLeave
from app.models.org import Organization
from app.models.user import User, UserStatus

BASE = "/api/developer/notification-leave/me"


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _today():
    return datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Shanghai")).date()


class NotificationLeaveApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()

        self.org = Organization(name="请假接口律所", code="leave_api")
        self.other_org = Organization(name="其它律所", code="leave_api_other")
        self.db.add_all([self.org, self.other_org])
        self.db.flush()

        self.user = self._user("leave-api-user", self.org.id)
        self.colleague = self._user("leave-api-colleague", self.org.id)
        self.outsider = self._user("leave-api-outsider", self.other_org.id)
        self.no_org = self._user("leave-api-no-org", None)
        self.db.commit()

        self.headers = {"Authorization": f"Bearer {create_access_token({'sub': self.user.id})}"}
        self.no_org_headers = {
            "Authorization": f"Bearer {create_access_token({'sub': self.no_org.id})}"
        }

        def override_get_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()
        self.engine.dispose()

    def _user(self, username, organization_id):
        user = User(
            username=username,
            email=f"{username}@example.com",
            hashed_password=hash_password("pw"),
            organization_id=organization_id,
            status=UserStatus.active.value,
        )
        self.db.add(user)
        self.db.flush()
        return user

    def _put(self, payload, headers=None):
        return self.client.put(BASE, json=payload, headers=headers or self.headers)

    def test_default_is_none(self):
        response = self.client.get(BASE, headers=self.headers)

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["status"], "none")
        self.assertIsNone(data["delegate_user_id"])
        self.assertIsNone(data["delegate_name"])
        self.assertIsNone(data["leave_start"])
        self.assertIsNone(data["days_left"])
        self.assertEqual(data["timezone"], "Asia/Shanghai")

    def test_set_and_read_active_leave(self):
        today = _today()
        response = self._put({
            "delegate_user_id": self.colleague.id,
            "leave_start": (today - timedelta(days=1)).isoformat(),
            "leave_end": (today + timedelta(days=10)).isoformat(),
            "note": "  出差  ",
        })

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(data["status"], "active")
        self.assertEqual(data["delegate_user_id"], self.colleague.id)
        self.assertEqual(data["delegate_name"], "leave-api-colleague")
        self.assertEqual(data["days_left"], 10)
        self.assertEqual(data["note"], "出差")

        listed = self.client.get(BASE, headers=self.headers).json()["data"]
        self.assertEqual(listed["status"], "active")
        self.assertEqual(listed["leave_end"], data["leave_end"])

    def test_scheduled_and_expired_status(self):
        today = _today()
        scheduled = self._put({
            "delegate_user_id": self.colleague.id,
            "leave_start": (today + timedelta(days=5)).isoformat(),
            "leave_end": (today + timedelta(days=9)).isoformat(),
        }).json()["data"]
        self.assertEqual(scheduled["status"], "scheduled")

        expired = self._put({
            "delegate_user_id": self.colleague.id,
            "leave_start": (today - timedelta(days=9)).isoformat(),
            "leave_end": (today - timedelta(days=1)).isoformat(),
        }).json()["data"]
        self.assertEqual(expired["status"], "expired")
        self.assertEqual(expired["days_left"], -1)

    def test_clearing_delegate_clears_window_and_note(self):
        today = _today()
        self._put({
            "delegate_user_id": self.colleague.id,
            "leave_start": today.isoformat(),
            "leave_end": (today + timedelta(days=3)).isoformat(),
            "note": "婚假",
        })

        data = self._put({
            "delegate_user_id": None,
            "leave_start": today.isoformat(),
            "leave_end": (today + timedelta(days=3)).isoformat(),
            "note": "婚假",
        }).json()["data"]

        self.assertEqual(data["status"], "none")
        self.assertIsNone(data["delegate_user_id"])
        self.assertIsNone(data["leave_start"])
        self.assertIsNone(data["leave_end"])
        self.assertIsNone(data["note"])

    def test_repeat_put_updates_single_row(self):
        self._put({"delegate_user_id": self.colleague.id, "note": "第一次"})
        self._put({"delegate_user_id": self.colleague.id, "note": "第二次"})

        self.assertEqual(self.db.query(LegalNotificationLeave).count(), 1)
        rows = self.client.get(BASE, headers=self.headers).json()["data"]
        self.assertEqual(rows["note"], "第二次")

    def test_rejects_other_org_delegate(self):
        response = self._put({"delegate_user_id": self.outsider.id})

        self.assertEqual(response.status_code, 422)
        self.assertIn("同组织", response.json()["detail"])

    def test_rejects_self_delegate(self):
        response = self._put({"delegate_user_id": self.user.id})

        self.assertEqual(response.status_code, 422)
        self.assertIn("自己", response.json()["detail"])

    def test_rejects_reversed_dates(self):
        today = _today()
        response = self._put({
            "delegate_user_id": self.colleague.id,
            "leave_start": (today + timedelta(days=5)).isoformat(),
            "leave_end": (today + timedelta(days=1)).isoformat(),
        })

        self.assertEqual(response.status_code, 422)
        self.assertIn("不能晚于", response.json()["detail"])

    def test_rejects_missing_delegate(self):
        response = self._put({"delegate_user_id": 999999})

        self.assertEqual(response.status_code, 422)

    def test_user_without_organization_is_rejected(self):
        self.assertEqual(self.client.get(BASE, headers=self.no_org_headers).status_code, 422)
        self.assertEqual(
            self._put({"delegate_user_id": None}, headers=self.no_org_headers).status_code,
            422,
        )

    def test_delete_clears_leave(self):
        self._put({"delegate_user_id": self.colleague.id, "note": "出差"})

        response = self.client.delete(BASE, headers=self.headers)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["status"], "none")
        self.assertEqual(
            self.client.get(BASE, headers=self.headers).json()["data"]["status"], "none",
        )

    def test_delete_without_leave_is_idempotent(self):
        response = self.client.delete(BASE, headers=self.headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["status"], "none")

    def test_note_length_is_validated(self):
        response = self._put({
            "delegate_user_id": self.colleague.id,
            "note": "请" * 201,
        })

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
