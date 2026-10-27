"""通知模板管理接口：管理员可见、预览不落库、草稿启用后生效、权限与错误码稳定。"""
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import create_access_token, hash_password
from app.core.database import Base, get_db
from app.main import app
from app.models.legal_notifications import NotificationTemplate
from app.models.user import User, UserStatus

BASE = "/api/admin/notification-templates"


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


class NotificationTemplateApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()

        self.admin = User(username="tpl_admin", email="tpl_admin@example.com",
                          hashed_password=hash_password("pw"), role="admin",
                          status=UserStatus.active.value)
        self.member = User(username="tpl_member", email="tpl_member@example.com",
                           hashed_password=hash_password("pw"), role="user",
                           status=UserStatus.active.value)
        self.db.add_all([self.admin, self.member])
        self.db.commit()
        self.admin_headers = {"Authorization": f"Bearer {create_access_token({'sub': self.admin.id})}"}
        self.member_headers = {"Authorization": f"Bearer {create_access_token({'sub': self.member.id})}"}

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

    def test_requires_admin(self):
        response = self.client.get(f"{BASE}/", headers=self.member_headers)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "ADMIN_REQUIRED")

    def test_lists_builtin_templates_and_layout_fields(self):
        response = self.client.get(f"{BASE}/", headers=self.admin_headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(len(data["builtin"]), 9)
        self.assertEqual({item["field"] for item in data["layout_fields"]},
                         {"subject", "lead", "action_label", "footnote"})
        self.assertEqual(data["templates"], [])

    def test_preview_layout_override_keeps_html(self):
        response = self.client.post(f"{BASE}/preview", headers=self.admin_headers, json={
            "template_key": "review_sla_due_soon",
            "body_format": "layout",
            "layout": {"lead": "预览导语", "action_label": "马上处理"},
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertIn("预览导语", data["html"])
        self.assertIn('role="presentation"', data["html"])
        self.assertIn("预览导语", data["text"])
        self.assertIn("subject", data)

    def test_preview_rejects_unknown_layout_field(self):
        response = self.client.post(f"{BASE}/preview", headers=self.admin_headers, json={
            "template_key": "review_sla_due_soon",
            "body_format": "layout",
            "layout": {"nope": "x"},
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "NOTIFICATION_TEMPLATE_INVALID")

    def test_preview_unknown_builtin_key_rejected(self):
        response = self.client.post(f"{BASE}/preview", headers=self.admin_headers, json={
            "template_key": "not_builtin",
            "body_format": "layout",
            "layout": {"lead": "x"},
        })
        self.assertEqual(response.status_code, 400)

    def test_create_draft_then_activate(self):
        created = self.client.post(f"{BASE}/", headers=self.admin_headers, json={
            "template_key": "consultation_returned",
            "body_format": "layout",
            "layout": {"lead": "运营文案：请补充依据"},
        })
        self.assertEqual(created.status_code, 200)
        draft = created.json()["data"]
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(draft["body_format"], "layout")
        self.assertEqual(draft["version"], 1)

        activated = self.client.post(f"{BASE}/{draft['id']}/activate", headers=self.admin_headers)
        self.assertEqual(activated.status_code, 200)
        self.assertEqual(activated.json()["data"]["status"], "active")

        listed = self.client.get(f"{BASE}/", headers=self.admin_headers).json()["data"]
        active = {item["template_key"]: item for item in listed["active_overrides"]}
        self.assertIn("consultation_returned", active)
        self.assertEqual(active["consultation_returned"]["version"], 1)

    def test_html_template_requires_body(self):
        response = self.client.post(f"{BASE}/", headers=self.admin_headers, json={
            "template_key": "consultation_returned",
            "body_format": "html",
            "body_template": "   ",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "NOTIFICATION_TEMPLATE_INVALID")

    def test_activate_missing_version_returns_404(self):
        response = self.client.post(f"{BASE}/999999/activate", headers=self.admin_headers)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "NOTIFICATION_TEMPLATE_NOT_FOUND")

    def test_activation_retires_old_version(self):
        first = self.client.post(f"{BASE}/", headers=self.admin_headers, json={
            "template_key": "review_sla_overdue", "body_format": "layout",
            "layout": {"lead": "v1 导语"},
        }).json()["data"]
        self.client.post(f"{BASE}/{first['id']}/activate", headers=self.admin_headers)
        second = self.client.post(f"{BASE}/", headers=self.admin_headers, json={
            "template_key": "review_sla_overdue", "body_format": "layout",
            "layout": {"lead": "v2 导语"},
        }).json()["data"]
        self.assertEqual(second["version"], 2)
        self.client.post(f"{BASE}/{second['id']}/activate", headers=self.admin_headers)

        rows = self.db.query(NotificationTemplate).filter(
            NotificationTemplate.template_key == "review_sla_overdue").all()
        statuses = {row.version: row.status for row in rows}
        self.assertEqual(statuses, {1: "retired", 2: "active"})


if __name__ == "__main__":
    unittest.main()
