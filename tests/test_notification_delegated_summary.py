"""「我代收了谁」汇总：按原收件人分组、未读数、最近时间、按人筛选。"""

import unittest
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import create_access_token, hash_password
from app.core.database import Base, get_db
from app.core.time import utc_now
from app.main import app
from app.models.legal_notifications import LegalNotificationEvent
from app.models.org import Organization
from app.models.user import User, UserStatus
from app.services.notification.notification_service import notification_service

BASE = "/api/developer/notifications"
SUMMARY = f"{BASE}/delegated-summary"


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


class DelegatedSummaryTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()

        self.org = Organization(name="代收汇总律所", code="delegated_summary")
        self.db.add(self.org)
        self.db.flush()
        self.user = self._user("summary-user", "代理人")
        self.alice = self._user("summary-alice", "主办律师")
        self.bob = self._user("summary-bob", None)
        self.carol = self._user("summary-carol", "第三人")
        self.db.commit()

        self.headers = {"Authorization": f"Bearer {create_access_token({'sub': self.user.id})}"}

        # Alice：2 条（1 未读 1 已读），最近 11-13
        self._event("Alice 未读", on_behalf=self.alice, status="delivered",
                    created_at=datetime(2026, 11, 12, 9, 0))
        self._event("Alice 已读", on_behalf=self.alice, status="read",
                    created_at=datetime(2026, 11, 13, 9, 0))
        # Bob：1 条未读，最近 11-11
        self._event("Bob 未读", on_behalf=self.bob, status="delivered",
                    created_at=datetime(2026, 11, 11, 9, 0))
        # 本人通知、失败事件、别人的代收事件都不计入
        self._event("本人通知", on_behalf=None, status="delivered")
        self._event("失败事件", on_behalf=self.alice, status="failed")
        self._event("别人的代收", on_behalf=self.alice, status="delivered", user=self.carol)

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

    def _event(self, title, *, on_behalf, status="delivered", created_at=None, user=None):
        event = LegalNotificationEvent(
            organization_id=self.org.id,
            user_id=(user or self.user).id,
            on_behalf_of_user_id=on_behalf.id if on_behalf else None,
            on_behalf_of_source="leave" if on_behalf else None,
            event_type="deadline",
            title=title,
            body="正文",
            channel="site",
            status=status,
            created_at=created_at or datetime(2026, 11, 10, 9, 0),
        )
        self.db.add(event)
        self.db.commit()
        return event

    def _summary(self, **params):
        response = self.client.get(SUMMARY, params=params, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]

    def _list(self, **params):
        response = self.client.get(f"{BASE}/me", params=params, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]

    def test_service_groups_by_origin(self):
        rows = notification_service.delegated_summary(db=self.db, user_id=self.user.id)

        self.assertEqual([row["user_id"] for row in rows], [self.alice.id, self.bob.id])
        self.assertEqual(rows[0]["count"], 2)
        self.assertEqual(rows[0]["unread"], 1)
        self.assertEqual(rows[0]["last_at"], datetime(2026, 11, 13, 9, 0))
        self.assertEqual(rows[1]["count"], 1)
        self.assertEqual(rows[1]["unread"], 1)

    def test_service_limit_keeps_biggest_group(self):
        rows = notification_service.delegated_summary(
            db=self.db, user_id=self.user.id, limit=1)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["user_id"], self.alice.id)

    def test_api_returns_names_and_times(self):
        data = self._summary()

        self.assertEqual(data["total"], 2)
        first = data["items"][0]
        self.assertEqual(first["user_id"], self.alice.id)
        self.assertEqual(first["name"], "主办律师")
        self.assertEqual(first["count"], 2)
        self.assertEqual(first["unread"], 1)
        self.assertTrue(first["last_at"].startswith("2026-11-13T09:00"))
        # 没有 full_name 时回落用户名
        self.assertEqual(data["items"][1]["name"], "summary-bob")

    def test_api_empty_summary(self):
        self.db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.user_id == self.user.id,
            LegalNotificationEvent.on_behalf_of_user_id.isnot(None),
        ).delete()
        self.db.commit()

        data = self._summary()

        self.assertEqual(data, {"items": [], "total": 0})

    def test_filter_by_origin(self):
        data = self._list(delegated_from_user_id=self.alice.id)

        self.assertEqual(data["total"], 2)
        self.assertEqual({item["on_behalf_of_user_id"] for item in data["items"]}, {self.alice.id})
        self.assertEqual({item["on_behalf_of_name"] for item in data["items"]}, {"主办律师"})

    def test_filter_by_origin_combines_with_delegation_source(self):
        self.assertEqual(
            self._list(delegated_from_user_id=self.alice.id, delegation_source="leave")["total"],
            2,
        )
        self.assertEqual(
            self._list(delegated_from_user_id=self.alice.id, delegation_source="event")["total"],
            0,
        )

    def test_filter_by_origin_rejects_zero(self):
        response = self.client.get(
            f"{BASE}/me", params={"delegated_from_user_id": 0}, headers=self.headers)
        self.assertEqual(response.status_code, 422, response.text)

    def test_summary_ignores_other_users_delegations(self):
        rows = notification_service.delegated_summary(db=self.db, user_id=self.carol.id)

        self.assertEqual([row["user_id"] for row in rows], [self.alice.id])

    def _reset_events(self):
        self.db.query(LegalNotificationEvent).delete()
        self.db.commit()

    def test_days_window_filters_old_events(self):
        self._reset_events()
        now = utc_now()
        self._event("Alice 十天前", on_behalf=self.alice, created_at=now - timedelta(days=10))
        self._event("Alice 昨天", on_behalf=self.alice, created_at=now - timedelta(days=1))
        self._event("Bob 两天前", on_behalf=self.bob, created_at=now - timedelta(days=2))

        week = self._summary(days=7)
        self.assertEqual(
            {item["name"]: item["count"] for item in week["items"]},
            {"主办律师": 1, "summary-bob": 1},
        )
        month = self._summary(days=30)
        self.assertEqual(
            {item["name"]: item["count"] for item in month["items"]},
            {"主办律师": 2, "summary-bob": 1},
        )
        self.assertEqual(
            {item["name"]: item["count"] for item in self._summary()["items"]},
            {"主办律师": 2, "summary-bob": 1},
        )

    def test_days_window_applies_to_list_and_count(self):
        self._reset_events()
        now = utc_now()
        self._event("旧通知", on_behalf=self.alice, created_at=now - timedelta(days=29))
        self._event("新通知", on_behalf=self.alice, created_at=now - timedelta(days=1))

        week = self._list(days=7)
        self.assertEqual(week["total"], 1)
        self.assertEqual([item["title"] for item in week["items"]], ["新通知"])

        month = self._list(days=30)
        self.assertEqual(month["total"], 2)

        self.assertEqual(self._summary(days=7)["items"][0]["count"], 1)

    def test_days_window_rejects_out_of_range(self):
        for value in (0, 366):
            response = self.client.get(
                f"{BASE}/me", params={"days": value}, headers=self.headers)
            self.assertEqual(response.status_code, 422, f"days={value}: {response.text}")
            summary = self.client.get(
                SUMMARY, params={"days": value}, headers=self.headers)
            self.assertEqual(summary.status_code, 422, f"summary days={value}")


if __name__ == "__main__":
    unittest.main()
