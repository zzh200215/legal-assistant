"""通知偏好接口：按事件类型读写、渠道/静默/时区校验、显式 null 清除。"""
import json
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import create_access_token, hash_password
from app.core.database import Base, get_db
from app.main import app
from app.models.legal_notifications import LegalNotificationPreference
from app.models.org import Organization
from app.models.user import User, UserStatus

BASE = "/api/developer/notification-preferences/me"


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


class NotificationPreferenceApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()

        self.org = Organization(name="偏好测试律所", code="pref_firm")
        self.other_org = Organization(name="其它律所", code="pref_other")
        self.db.add_all([self.org, self.other_org])
        self.db.flush()

        self.user = self._user("pref-user", self.org.id)
        self.colleague = self._user("pref-colleague", self.org.id)
        self.outsider = self._user("pref-outsider", self.other_org.id)
        self.no_org = self._user("pref-no-org", None)
        self.db.commit()

        self.headers = {"Authorization": f"Bearer {create_access_token({'sub': self.user.id})}"}
        self.no_org_headers = {"Authorization": f"Bearer {create_access_token({'sub': self.no_org.id})}"}

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

    def _put(self, event_type, payload, headers=None):
        return self.client.put(
            BASE, params={"event_type": event_type}, json=payload,
            headers=headers or self.headers,
        )

    def test_empty_list(self):
        response = self.client.get(BASE, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"], [])

    def test_create_and_read_preference(self):
        response = self._put("deadline", {
            "channels_json": json.dumps(["site", "email"]),
            "mute_start": "22:00",
            "mute_end": "07:30",
            "timezone": "Asia/Tokyo",
            "delegate_user_id": self.colleague.id,
            "delegate_start": "2026-11-10",
            "delegate_end": "2026-11-20",
            "summary_frequency": "daily",
        })
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(data["event_type"], "deadline")
        self.assertEqual(data["organization_id"], self.org.id)
        self.assertEqual(json.loads(data["channels_json"]), ["site", "email"])
        self.assertEqual(data["mute_start"], "22:00")
        self.assertEqual(data["timezone"], "Asia/Tokyo")
        self.assertEqual(data["delegate_user_id"], self.colleague.id)
        self.assertEqual(data["delegate_start"], "2026-11-10")
        self.assertEqual(data["delegate_end"], "2026-11-20")
        self.assertEqual(data["summary_frequency"], "daily")

        listed = self.client.get(BASE, headers=self.headers).json()["data"]
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["event_type"], "deadline")

    def test_delegate_window_rejects_reversed_dates_and_can_clear(self):
        self._put("deadline", {
            "delegate_user_id": self.colleague.id,
            "delegate_start": "2026-11-10",
            "delegate_end": "2026-11-20",
        })
        bad = self._put("deadline", {"delegate_start": "2026-11-21"})
        self.assertEqual(bad.status_code, 422)

        cleared = self._put("deadline", {"delegate_start": None, "delegate_end": None})
        self.assertEqual(cleared.status_code, 200)
        data = cleared.json()["data"]
        self.assertIsNone(data["delegate_start"])
        self.assertIsNone(data["delegate_end"])

    def test_clearing_delegate_also_clears_window(self):
        self._put("deadline", {
            "delegate_user_id": self.colleague.id,
            "delegate_start": "2026-11-10",
            "delegate_end": "2026-11-20",
        })
        response = self._put("deadline", {"delegate_user_id": None})
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertIsNone(data["delegate_user_id"])
        self.assertIsNone(data["delegate_start"])
        self.assertIsNone(data["delegate_end"])

    def test_partial_update_keeps_other_fields(self):
        self._put("approval", {
            "channels_json": json.dumps(["site"]),
            "mute_start": "12:00",
            "mute_end": "13:00",
            "timezone": "Asia/Shanghai",
        })
        response = self._put("approval", {"summary_frequency": "weekly"})
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["mute_start"], "12:00")
        self.assertEqual(data["timezone"], "Asia/Shanghai")
        self.assertEqual(data["summary_frequency"], "weekly")

    def test_null_clears_mute_and_delegate(self):
        self._put("deadline", {
            "mute_start": "22:00",
            "mute_end": "08:00",
            "delegate_user_id": self.colleague.id,
        })
        response = self._put("deadline", {"mute_start": None, "mute_end": "", "delegate_user_id": None})
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertIsNone(data["mute_start"])
        self.assertIsNone(data["mute_end"])
        self.assertIsNone(data["delegate_user_id"])

    def test_rejects_unknown_event_type(self):
        response = self._put("unknown_type", {"summary_frequency": "none"})
        self.assertEqual(response.status_code, 422)

    def test_rejects_bad_channels(self):
        for payload in (
            {"channels_json": "not-json"},
            {"channels_json": json.dumps(["site", "sms"])},
            {"channels_json": json.dumps({"site": True})},
        ):
            response = self._put("deadline", payload)
            self.assertEqual(response.status_code, 422, payload)

    def test_rejects_bad_timezone(self):
        response = self._put("deadline", {"timezone": "Mars/Olympus"})
        self.assertEqual(response.status_code, 422)

    def test_rejects_bad_mute(self):
        for value in ("25:00", "9:00", "22:60", "abc"):
            response = self._put("deadline", {"mute_start": value})
            self.assertEqual(response.status_code, 422, value)

    def test_rejects_bad_summary_frequency(self):
        response = self._put("deadline", {"summary_frequency": "hourly"})
        self.assertEqual(response.status_code, 422)

    def test_delegate_must_be_same_org(self):
        response = self._put("deadline", {"delegate_user_id": self.outsider.id})
        self.assertEqual(response.status_code, 422)

    def test_user_without_org_cannot_save(self):
        response = self._put("deadline", {"summary_frequency": "none"}, headers=self.no_org_headers)
        self.assertEqual(response.status_code, 422)

    def test_get_only_returns_current_org(self):
        self.db.add(LegalNotificationPreference(
            user_id=self.user.id, organization_id=self.other_org.id,
            event_type="invoice", channels_json='["site"]',
        ))
        self.db.commit()
        listed = self.client.get(BASE, headers=self.headers).json()["data"]
        self.assertEqual(listed, [])


if __name__ == "__main__":
    unittest.main()
