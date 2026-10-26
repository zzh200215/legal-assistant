"""HTML 邮件版式：通知邮件生成 multipart/alternative（纯文本 + HTML），模板缺失仍可发。

覆盖三层：内置模板渲染 → 草稿落 content_html → SMTP 报文结构；以及回退与转义。
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import hash_password
from app.core.config import get_settings
from app.core.database import Base
from app.models.email import EmailDraft, EmailSendRequest
from app.models.org import Organization
from app.models.user import User, UserStatus
from app.services.notification.email_templates import (
    BUILTIN_EMAIL_TEMPLATES, action_url, render_builtin_email,
)
from app.services.notification.notification_service import notification_service
from app.services.notification.outbound_email_service import outbound_email_service


class CapturingSMTP:
    sent = []

    def __init__(self, host, port, timeout=20):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def ehlo(self):
        return None

    def starttls(self):
        return None

    def login(self, username, password):
        pass

    def send_message(self, message):
        CapturingSMTP.sent.append(message)


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


class BuiltinEmailTemplateTests(unittest.TestCase):
    """内置模板注册表：7 个业务键、转义、深链可选。"""

    EXPECTED_KEYS = {
        "review_sla_due_soon", "review_sla_overdue",
        "review_consultation_uncited", "review_consultation_weak",
        "consultation_returned_supplement", "consultation_returned",
        "consultation_supplemented",
    }

    def test_all_business_template_keys_registered(self):
        self.assertEqual(set(BUILTIN_EMAIL_TEMPLATES), self.EXPECTED_KEYS)
        for key in sorted(self.EXPECTED_KEYS):
            rendered = render_builtin_email(key, params={"title": "标题", "body": "正文"})
            self.assertIsNotNone(rendered, key)
            self.assertTrue(rendered["subject"].startswith("【律智检】"), key)
            self.assertIn("标题", rendered["text"], key)
            self.assertIn("<!DOCTYPE html>", rendered["html"], key)

    def test_unknown_key_returns_none(self):
        self.assertIsNone(render_builtin_email("not_registered", params={"title": "t"}))

    def test_html_is_escaped(self):
        rendered = render_builtin_email(
            "consultation_returned",
            params={"title": '<b>危险</b>', "body": '<script>alert("x")</script> & 文本'},
        )
        self.assertNotIn("<script>", rendered["html"])
        self.assertNotIn("<b>危险</b>", rendered["html"])
        self.assertIn("&lt;script&gt;", rendered["html"])
        self.assertIn("&amp;", rendered["html"])
        # 纯文本版保留原文（不转义是给人读的）
        self.assertIn("<script>", rendered["text"])

    def test_action_url_absent_without_base_url(self):
        with patch.object(get_settings(), "NOTIFICATION_EMAIL_ACTION_BASE_URL", ""):
            self.assertIsNone(action_url("contract_review"))
            rendered = render_builtin_email("review_sla_due_soon", params={"title": "t", "body": "b"})
        self.assertNotIn("<a href=", rendered["html"])
        self.assertNotIn("http", rendered["text"])

    def test_action_url_configured_maps_reference_type(self):
        with patch.object(get_settings(), "NOTIFICATION_EMAIL_ACTION_BASE_URL", "https://legal.example.com/"):
            self.assertEqual(action_url("consultation"),
                             "https://legal.example.com/legal-workspace?view=review")
            self.assertEqual(action_url(None), "https://legal.example.com/notifications")
            rendered = render_builtin_email(
                "consultation_returned_supplement",
                params={"title": "t", "body": "b", "action_url": action_url("consultation")},
            )
        self.assertIn("https://legal.example.com/legal-workspace?view=review", rendered["html"])
        self.assertIn("去补充依据", rendered["text"])


class NotificationEmailDispatchTests(unittest.TestCase):
    """通知 Outbox → 草稿 content_html → SMTP multipart/alternative。"""

    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.Session = Session
        self.db = Session()

        org = Organization(name="HtmlMailOrg", code="HTML")
        self.db.add(org)
        self.db.flush()
        self.org_id = org.id

        self.user = User(username="html_u1", email="html_u1@example.com",
                         hashed_password=hash_password("pw"), role="user",
                         status=UserStatus.active.value, organization_id=org.id)
        self.db.add(self.user)
        self.db.commit()
        self.user_id = self.user.id

        outbound_email_service.update_policy(
            db=self.db, user=self.user,
            request=SimpleNamespace(enabled=True, allowed_recipient_domains=["example.com"],
                                    max_sends_per_hour=100, require_approval=True,
                                    dlp_enabled=True, dlp_action="block"),
        )
        outbound_email_service.create_smtp_connector(
            db=self.db, user=self.user,
            request=SimpleNamespace(name="smtp", host="smtp.example.com", port=587,
                                    username="html_u1@example.com", password="pw",
                                    from_address="html_u1@example.com", use_starttls=True),
        )
        CapturingSMTP.sent = []

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _create(self, template_key="review_sla_due_soon"):
        return notification_service.create_notification(
            db=self.db, organization_id=self.org_id, user_id=self.user_id,
            event_type="approval", title="审核即将到期：合同 A（剩 6 小时）",
            body="sla:due_soon · 合同审查 #1 的审核截止时间 2026-10-27 09:00",
            channel="email", reference_type="contract_review", reference_id=1,
            template_key=template_key,
        )

    def test_dispatch_stores_html_version_in_draft(self):
        event = self._create()
        with patch("app.services.notification.outbound_email_service.smtplib.SMTP", CapturingSMTP):
            notification_service.dispatch_pending(db=self.db)
        request = self.db.query(EmailSendRequest).filter(
            EmailSendRequest.notification_event_id == event.id).first()
        self.assertIsNotNone(request)
        draft = self.db.query(EmailDraft).filter(EmailDraft.id == request.draft_id).first()
        self.assertIsNotNone(draft.content_html)
        self.assertIn("<!DOCTYPE html>", draft.content_html)
        self.assertEqual(len(CapturingSMTP.sent), 0, "dispatch 只登记 Outbox")

    def test_worker_sends_multipart_alternative(self):
        event = self._create()
        with patch("app.services.notification.outbound_email_service.smtplib.SMTP", CapturingSMTP):
            notification_service.dispatch_pending(db=self.db)
            owner = "html-worker"
            for request in outbound_email_service.claim_pending_batch(db=self.db, owner=owner):
                outbound_email_service._perform_send(db=self.db, request=request, owner=owner)
            self.db.commit()
        self.assertEqual(len(CapturingSMTP.sent), 1)
        message = CapturingSMTP.sent[0]
        self.assertTrue(message.is_multipart())
        subtypes = [part.get_content_subtype() for part in message.iter_parts()]
        self.assertEqual(subtypes, ["plain", "html"])
        html_part = [part for part in message.iter_parts() if part.get_content_subtype() == "html"][0]
        self.assertIn("审核即将到期", html_part.get_content())
        self.db.refresh(event)
        self.assertEqual(event.status, "sent")

    def test_template_missing_sends_plain_text_only(self):
        event = self._create(template_key="unregistered_key")
        with patch("app.services.notification.outbound_email_service.smtplib.SMTP", CapturingSMTP):
            notification_service.dispatch_pending(db=self.db)
            owner = "plain-worker"
            for request in outbound_email_service.claim_pending_batch(db=self.db, owner=owner):
                outbound_email_service._perform_send(db=self.db, request=request, owner=owner)
            self.db.commit()
        self.assertEqual(len(CapturingSMTP.sent), 1)
        message = CapturingSMTP.sent[0]
        self.assertFalse(message.is_multipart())
        self.assertEqual(message.get_content_subtype(), "plain")
        self.db.refresh(event)
        self.assertEqual(event.status, "sent")

    def test_configured_base_url_adds_cta_link(self):
        with patch.object(get_settings(), "NOTIFICATION_EMAIL_ACTION_BASE_URL",
                          "https://legal.example.com"):
            event = self._create()
            with patch("app.services.notification.outbound_email_service.smtplib.SMTP", CapturingSMTP):
                notification_service.dispatch_pending(db=self.db)
                owner = "cta-worker"
                for request in outbound_email_service.claim_pending_batch(db=self.db, owner=owner):
                    outbound_email_service._perform_send(db=self.db, request=request, owner=owner)
                self.db.commit()
        html_part = [part for part in CapturingSMTP.sent[0].iter_parts()
                     if part.get_content_subtype() == "html"][0]
        self.assertIn("https://legal.example.com/legal-workspace?view=review", html_part.get_content())
        self.db.refresh(event)
        self.assertEqual(event.status, "sent")


if __name__ == "__main__":
    unittest.main()
