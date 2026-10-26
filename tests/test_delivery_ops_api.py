"""投递看板 API：管理员可读、非管理员 403；死信重排的稳定错误码。"""
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import create_access_token, hash_password
from app.core.database import Base, get_db
from app.main import app
from app.models.email import EmailSendRequest
from app.models.legal_notifications import LegalNotificationEvent
from app.models.org import Organization
from app.models.user import User, UserStatus


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


class OutboxOverviewApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()

        org = Organization(name="ApiOpsOrg", code="APIOPS")
        self.db.add(org)
        self.db.flush()
        self.org_id = org.id

        self.admin = User(username="api_ops_admin", email="api_ops_admin@example.com",
                          hashed_password=hash_password("pw"), role="admin",
                          status=UserStatus.active.value, organization_id=org.id)
        self.member = User(username="api_ops_member", email="api_ops_member@example.com",
                           hashed_password=hash_password("pw"), role="user",
                           status=UserStatus.active.value, organization_id=org.id)
        self.db.add_all([self.admin, self.member])
        self.db.commit()
        self.admin_token = create_access_token({"sub": self.admin.id})
        self.member_token = create_access_token({"sub": self.member.id})

        def override_get_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

        self.event = LegalNotificationEvent(
            organization_id=self.org_id, user_id=self.admin.id, event_type="approval",
            title="审核即将到期：合同 A", body="sla:due_soon", channel="email",
            status="dead_letter", attempt=5, error_code="TEMPLATE_ERROR",
            template_key="review_sla_due_soon", reference_type="contract_review",
            reference_id=1,
        )
        self.db.add(self.event)
        self.db.add(EmailSendRequest(
            draft_id=1, smtp_connector_id=1, user_id=self.admin.id,
            organization_id=self.org_id, recipient="client@example.com",
            subject="死信邮件", content_hash="api-h1", idempotency_key="api-ops-1",
            status="dead_letter", attempt=5, error_code="SMTP_AUTH",
        ))
        self.db.commit()
        self.event_id = self.event.id

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()
        self.engine.dispose()

    def _headers(self, token):
        return {"Authorization": f"Bearer {token}"}

    def test_requires_admin(self):
        response = self.client.get("/api/analytics/outbox/overview",
                                   headers=self._headers(self.member_token))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "ADMIN_REQUIRED")

    def test_overview_shape(self):
        response = self.client.get("/api/analytics/outbox/overview?days=7",
                                   headers=self._headers(self.admin_token))
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["window"]["days"], 7)
        self.assertEqual(data["notification"]["dead_letter"], 1)
        self.assertEqual(data["email"]["dead_letter"], 1)
        self.assertTrue(any(alert["code"] == "DEAD_LETTER" for alert in data["alerts"]))
        self.assertFalse(data["health"]["healthy"])
        self.assertEqual(data["trend"]["days"], 7)
        self.assertEqual(len(data["trend"]["notification"]), 7)
        self.assertEqual(data["notification"]["dead_letters"]["items"][0]["error_code"], "TEMPLATE_ERROR")

    def test_retry_notification_and_email_dead_letter(self):
        response = self.client.post(
            f"/api/analytics/outbox/dead-letters/notification/{self.event_id}/retry",
            headers=self._headers(self.admin_token),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["status"], "pending")

        email_id = self.db.query(EmailSendRequest).filter(
            EmailSendRequest.idempotency_key == "api-ops-1").first().id
        response = self.client.post(
            f"/api/analytics/outbox/dead-letters/email/{email_id}/retry",
            headers=self._headers(self.admin_token),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["status"], "pending")

    def test_retry_missing_item_returns_404(self):
        response = self.client.post(
            "/api/analytics/outbox/dead-letters/notification/999999/retry",
            headers=self._headers(self.admin_token),
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "OUTBOX_ITEM_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
