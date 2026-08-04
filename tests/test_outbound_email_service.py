import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.email import EmailDraft
from app.models.user import User
from app.services.outbound_email_service import outbound_email_service


class FakeSMTP:
    sent_messages = []

    def __init__(self, host, port, timeout=20):
        self.host = host
        self.port = port

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def ehlo(self):
        return None

    def starttls(self):
        return None

    def login(self, username, password):
        self.username = username

    def send_message(self, message):
        self.sent_messages.append(message)
        return {}


class OutboundEmailServiceTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        self.db = SessionLocal()
        self.user = User(username="sender", email="sender@example.com", hashed_password="secret")
        self.db.add(self.user)
        self.db.commit(); self.db.refresh(self.user)
        self.approver = User(username="approver", email="approver@example.com", hashed_password="secret", role="admin")
        self.db.add(self.approver)
        self.db.commit(); self.db.refresh(self.approver)
        self.draft = EmailDraft(
            user_id=self.user.id, subject="项目同步", recipient="team@example.com", content="请确认本周进度。", status="draft", generation_type="generate", tone="professional"
        )
        self.db.add(self.draft); self.db.commit(); self.db.refresh(self.draft)
        outbound_email_service.update_policy(
            db=self.db, user=self.user,
            request=SimpleNamespace(enabled=True, allowed_recipient_domains=["example.com"], max_sends_per_hour=2, require_approval=True, dlp_enabled=True, dlp_action="block"),
        )
        self.connector = outbound_email_service.create_smtp_connector(
            db=self.db, user=self.user,
            request=SimpleNamespace(name="企业 SMTP", host="smtp.example.com", port=587, username="sender@example.com", password="app-password", from_address="sender@example.com", use_starttls=True),
        )

    def tearDown(self):
        self.db.close()

    def test_approval_and_smtp_send_are_idempotent(self):
        request = outbound_email_service.request_send(self.draft.id, connector_id=self.connector.id, db=self.db, user=self.user)
        self.assertEqual(request.status, "pending")
        with self.assertRaises(ValueError):
            outbound_email_service.execute_request(request.id, db=self.db, user=self.user)
        with self.assertRaisesRegex(ValueError, "Only organization administrators"):
            outbound_email_service.decide_request(request.id, approved=True, note=None, db=self.db, user=self.user)
        approved = outbound_email_service.decide_request(request.id, approved=True, note="已核对收件人", db=self.db, user=self.approver)
        self.assertEqual(approved.status, "approved")
        self.assertEqual(approved.approved_by_user_id, self.approver.id)
        FakeSMTP.sent_messages = []
        with patch("app.services.outbound_email_service.smtplib.SMTP", FakeSMTP):
            sent = outbound_email_service.execute_request(request.id, db=self.db, user=self.user)
            repeated = outbound_email_service.execute_request(request.id, db=self.db, user=self.user)
        self.assertEqual(sent.status, "sent")
        self.assertEqual(repeated.id, sent.id)
        self.assertEqual(len(FakeSMTP.sent_messages), 1)
        self.assertTrue(sent.provider_message_id)

    def test_changed_draft_and_kill_switch_block_execution(self):
        request = outbound_email_service.request_send(self.draft.id, connector_id=self.connector.id, db=self.db, user=self.user)
        outbound_email_service.decide_request(request.id, approved=True, note=None, db=self.db, user=self.approver)
        self.draft.content = "草稿已修改"
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "草稿内容已变更"):
            outbound_email_service.execute_request(request.id, db=self.db, user=self.user)

        next_request = outbound_email_service.request_send(self.draft.id, connector_id=self.connector.id, db=self.db, user=self.user)
        outbound_email_service.decide_request(next_request.id, approved=True, note=None, db=self.db, user=self.approver)
        outbound_email_service.update_policy(
            db=self.db, user=self.user,
            request=SimpleNamespace(enabled=False, allowed_recipient_domains=["example.com"], max_sends_per_hour=2, require_approval=True, dlp_enabled=True, dlp_action="block"),
        )
        with self.assertRaisesRegex(ValueError, "已停用"):
            outbound_email_service.execute_request(next_request.id, db=self.db, user=self.user)

    def test_administrator_cannot_approve_own_or_other_organization_request(self):
        self.user.role = "admin"
        self.db.commit()
        own_request = outbound_email_service.request_send(self.draft.id, connector_id=self.connector.id, db=self.db, user=self.user)
        with self.assertRaisesRegex(ValueError, "Requesters cannot decide"):
            outbound_email_service.decide_request(own_request.id, approved=True, note=None, db=self.db, user=self.user)

        outside = User(username="outside", email="outside@example.com", hashed_password="secret", role="admin", organization_id=99)
        self.db.add(outside)
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "outside your organization"):
            outbound_email_service.decide_request(own_request.id, approved=True, note=None, db=self.db, user=outside)

    def test_dlp_blocks_high_risk_secret_before_approval_and_keeps_only_masked_findings(self):
        self.draft.content = "请使用测试令牌 sk_abcdefghijklmnopqrstuvwxyz123456 访问接口。"
        self.db.commit()
        request = outbound_email_service.request_send(self.draft.id, connector_id=self.connector.id, db=self.db, user=self.user)
        self.assertEqual(request.status, "blocked")
        self.assertEqual(request.dlp_status, "blocked")
        serialized = outbound_email_service.serialize_request(request, db=self.db, viewer=self.user)
        self.assertEqual(serialized["dlp_findings"][0]["code"], "api_token")
        self.assertNotIn("sk_abcdefghijklmnopqrstuvwxyz123456", json.dumps(serialized["dlp_findings"], ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
