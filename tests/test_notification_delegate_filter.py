"""通知中心代收筛选：delegated=true/false 与代收人姓名下发。"""

import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import create_access_token, hash_password
from app.core.database import Base, get_db
from app.main import app
from app.models.legal_notifications import LegalNotificationEvent
from app.models.org import Organization
from app.models.user import User, UserStatus

BASE = "/api/developer/notifications/me"


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


class DelegateNotificationFilterTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()

        self.org = Organization(name="代收筛选律所", code="delegate_filter")
        self.db.add(self.org)
        self.db.flush()
        self.user = self._user("filter-user", "接收人")
        self.origin_a = self._user("filter-origin-a", "原律师甲")
        self.origin_b = self._user("filter-origin-b", None)
        self.db.commit()

        self.headers = {"Authorization": f"Bearer {create_access_token({'sub': self.user.id})}"}
        self._event("本人通知", on_behalf=None)
        self._event("代收通知甲", on_behalf=self.origin_a)
        self._event("代收通知乙", on_behalf=self.origin_b)

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

    def _user(self, username, full_name):
        user = User(
            username=username,
            email=f"{username}@example.com",
            hashed_password=hash_password("pw"),
            full_name=full_name,
            organization_id=self.org.id,
            status=UserStatus.active.value,
        )
        self.db.add(user)
        self.db.flush()
        return user

    def _event(self, title, *, on_behalf):
        event = LegalNotificationEvent(
            organization_id=self.org.id,
            user_id=self.user.id,
            on_behalf_of_user_id=on_behalf.id if on_behalf else None,
            event_type="deadline",
            title=title,
            body="正文",
            channel="site",
            status="delivered",
        )
        self.db.add(event)
        self.db.commit()
        return event

    def _get(self, **params):
        response = self.client.get(BASE, params=params, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]

    def test_all_returns_both(self):
        data = self._get()
        self.assertEqual(data["total"], 3)
        self.assertEqual(len(data["items"]), 3)

    def test_only_delegated(self):
        data = self._get(delegated="true")
        self.assertEqual(data["total"], 2)
        self.assertEqual({item["title"] for item in data["items"]}, {"代收通知甲", "代收通知乙"})
        names = {item["on_behalf_of_name"] for item in data["items"]}
        self.assertEqual(names, {"原律师甲", "filter-origin-b"})

    def test_only_own(self):
        data = self._get(delegated="false")
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["title"], "本人通知")
        self.assertIsNone(data["items"][0]["on_behalf_of_name"])

    def test_filter_combines_with_event_type_and_pagination(self):
        data = self._get(delegated="true", event_type="deadline", page=1, page_size=1)
        self.assertEqual(data["total"], 2)
        self.assertEqual(len(data["items"]), 1)

        other = self._get(delegated="true", event_type="approval")
        self.assertEqual(other["total"], 0)
        self.assertEqual(other["items"], [])


if __name__ == "__main__":
    unittest.main()
