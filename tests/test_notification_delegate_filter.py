"""通知中心代收筛选：delegated=true/false、delegation_source 细分与代收人姓名下发。"""

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
        self._event("请假代收通知", on_behalf=self.origin_a, source="leave")
        self._event("类型代收通知", on_behalf=self.origin_b, source="event")
        # 历史事件：有代收人但没有来源列（上线前创建），按 unknown 归类
        self._event("历史代收通知", on_behalf=self.origin_a)

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

    def _event(self, title, *, on_behalf, source=None):
        event = LegalNotificationEvent(
            organization_id=self.org.id,
            user_id=self.user.id,
            on_behalf_of_user_id=on_behalf.id if on_behalf else None,
            on_behalf_of_source=source if on_behalf else None,
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
        self.assertEqual(data["total"], 4)
        self.assertEqual(len(data["items"]), 4)

    def test_only_delegated(self):
        data = self._get(delegated="true")
        self.assertEqual(data["total"], 3)
        self.assertEqual(
            {item["title"] for item in data["items"]},
            {"请假代收通知", "类型代收通知", "历史代收通知"},
        )
        names = {item["on_behalf_of_name"] for item in data["items"]}
        self.assertEqual(names, {"原律师甲", "filter-origin-b"})

    def test_payload_carries_delegation_source(self):
        data = self._get()
        sources = {item["title"]: item["on_behalf_of_source"] for item in data["items"]}
        self.assertEqual(sources, {
            "本人通知": None,
            "请假代收通知": "leave",
            "类型代收通知": "event",
            "历史代收通知": None,
        })

    def test_filter_by_leave_source(self):
        data = self._get(delegation_source="leave")
        self.assertEqual(data["total"], 1)
        self.assertEqual([item["title"] for item in data["items"]], ["请假代收通知"])

    def test_filter_by_event_source(self):
        data = self._get(delegation_source="event")
        self.assertEqual(data["total"], 1)
        self.assertEqual([item["title"] for item in data["items"]], ["类型代收通知"])

    def test_filter_by_unknown_source_is_legacy_delegated(self):
        data = self._get(delegation_source="unknown")
        self.assertEqual(data["total"], 1)
        self.assertEqual([item["title"] for item in data["items"]], ["历史代收通知"])

    def test_unsupported_source_rejected(self):
        response = self.client.get(
            BASE, params={"delegation_source": "escalation"}, headers=self.headers)
        self.assertEqual(response.status_code, 422, response.text)

    def test_only_own(self):
        data = self._get(delegated="false")
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["title"], "本人通知")
        self.assertIsNone(data["items"][0]["on_behalf_of_name"])

    def test_filter_combines_with_event_type_and_pagination(self):
        data = self._get(delegated="true", event_type="deadline", page=1, page_size=2)
        self.assertEqual(data["total"], 3)
        self.assertEqual(len(data["items"]), 2)

        other = self._get(delegated="true", event_type="approval")
        self.assertEqual(other["total"], 0)
        self.assertEqual(other["items"], [])

    def test_source_filter_combines_with_other_filters(self):
        data = self._get(delegation_source="leave", event_type="approval")
        self.assertEqual(data["total"], 0)
        combined = self._get(delegation_source="leave", delegated="true", page=1, page_size=10)
        self.assertEqual(combined["total"], 1)


if __name__ == "__main__":
    unittest.main()
