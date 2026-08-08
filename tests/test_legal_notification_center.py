"""US-002 — 最小站内通知中心：列表 + 未读数 + 单条/全部已读"""
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import hash_password, create_access_token
from app.core.database import Base, get_db
from app.main import app
from app.models.user import User, UserStatus
from app.models.org import Organization
from app.models.legal_notifications import LegalNotificationEvent
from fastapi.testclient import TestClient


def _make_engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


class NotificationCenterTests(unittest.TestCase):
    """/api/developer/notifications/* 端点"""

    def setUp(self):
        self.engine = _make_engine()
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session()

        org = Organization(name="NotifyCenterOrg", code="NCEN")
        self.db.add(org)
        self.db.flush()

        self.user = User(
            username="notify_center",
            email="nc@t.com",
            hashed_password=hash_password("pw"),
            role="user",
            status=UserStatus.active.value,
            organization_id=org.id,
        )
        self.db.add(self.user)
        self.db.commit()

        self.org_id = org.id
        self.user_id = self.user.id

        def _override_db():
            yield self.db

        app.dependency_overrides[get_db] = _override_db
        token = create_access_token({"sub": str(self.user.id)})
        self.client = TestClient(app, raise_server_exceptions=False)
        self.headers = {"Authorization": f"Bearer {token}"}

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def _add_event(self, status: str = "delivered", title: str = "门户到期") -> LegalNotificationEvent:
        ev = LegalNotificationEvent(
            organization_id=self.org_id,
            user_id=self.user_id,
            case_id=None,
            event_type="portal",
            title=title,
            channel="site",
            status=status,
        )
        self.db.add(ev)
        self.db.commit()
        self.db.refresh(ev)
        return ev

    def test_list_returns_events_and_unread_count(self):
        self._add_event(status="delivered", title="客户门户链接已到期：案件A")
        self._add_event(status="sent", title="门户链接即将到期：案件B")
        self._add_event(status="read", title="已读通知")
        self._add_event(status="failed", title="失败通知")

        resp = self.client.get("/api/developer/notifications/me", headers=self.headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()["data"]
        self.assertEqual(data["unread"], 2)  # delivered + sent
        items = data["items"]
        self.assertEqual(len(items), 3)  # failed 被过滤
        self.assertEqual({i["status"] for i in items}, {"delivered", "sent", "read"})

    def test_list_requires_auth(self):
        resp = self.client.get("/api/developer/notifications/me")
        self.assertEqual(resp.status_code, 401)

    def test_mark_single_read(self):
        ev = self._add_event(status="delivered")
        resp = self.client.post(f"/api/developer/notifications/{ev.id}/read", headers=self.headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.db.refresh(ev)
        self.assertEqual(ev.status, "read")

    def test_mark_read_nonexistent_returns_404(self):
        resp = self.client.post("/api/developer/notifications/999999/read", headers=self.headers)
        self.assertEqual(resp.status_code, 404)

    def test_mark_read_other_users_event_404(self):
        ev = self._add_event(status="delivered")
        other = User(
            username="other_user",
            email="other@t.com",
            hashed_password=hash_password("pw"),
            role="user",
            status=UserStatus.active.value,
        )
        self.db.add(other)
        self.db.commit()
        other_token = create_access_token({"sub": str(other.id)})
        resp = self.client.post(
            f"/api/developer/notifications/{ev.id}/read",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_mark_all_read(self):
        self._add_event(status="delivered")
        self._add_event(status="sent")
        resp = self.client.post("/api/developer/notifications/read-all", headers=self.headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()["data"]
        self.assertEqual(data["updated"], 2)
        resp2 = self.client.get("/api/developer/notifications/me", headers=self.headers)
        self.assertEqual(resp2.json()["data"]["unread"], 0)


if __name__ == "__main__":
    unittest.main()
