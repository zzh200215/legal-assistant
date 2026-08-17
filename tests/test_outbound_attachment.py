"""外发邮件附件测试：上传安全（大小/MIME/DLP）、发送时附件随邮件发出。"""
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import hash_password
from app.core.config import get_settings
from app.core.database import Base
from app.models.email import EmailAttachment, EmailDraft
from app.models.org import Organization
from app.models.user import User, UserStatus
from app.services.notification.outbound_email_service import outbound_email_service

VALID_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


class FakeUploadFile:
    def __init__(self, filename, content):
        self.filename = filename
        self.file = BytesIO(content)


class FakeSMTP:
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
        FakeSMTP.sent.append(message)


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


class OutboundAttachmentTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)()
        org = Organization(name="AttachOrg", code="ATCH")
        self.db.add(org)
        self.db.flush()
        self.org_id = org.id
        self.user = User(username="sender", email="sender@example.com",
                         hashed_password=hash_password("pw"), role="user",
                         status=UserStatus.active.value, organization_id=org.id)
        self.db.add(self.user)
        self.db.flush()
        self.user_id = self.user.id
        self.approver = User(username="approver", email="approver@example.com",
                             hashed_password=hash_password("pw"), role="admin",
                             status=UserStatus.active.value, organization_id=org.id)
        self.db.add(self.approver)
        self.db.commit()

        outbound_email_service.update_policy(
            db=self.db, user=self.user,
            request=SimpleNamespace(enabled=True, allowed_recipient_domains=["example.com"],
                                    max_sends_per_hour=100, require_approval=True,
                                    dlp_enabled=True, dlp_action="block"),
        )
        self.connector = outbound_email_service.create_smtp_connector(
            db=self.db, user=self.user,
            request=SimpleNamespace(name="smtp", host="smtp.example.com", port=587,
                                    username="sender@example.com", password="pw",
                                    from_address="sender@example.com", use_starttls=True),
        )
        self.draft = EmailDraft(user_id=self.user_id, organization_id=self.org_id,
                                subject="附合同", recipient="team@example.com",
                                content="请查收附件。", status="draft",
                                generation_type="generate", tone="professional")
        self.db.add(self.draft)
        self.db.commit()
        self.db.refresh(self.draft)
        FakeSMTP.sent = []

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_upload_clean_attachment_and_send_with_attachment(self):
        row = outbound_email_service.upload_attachment(
            db=self.db, user=self.user, draft_id=self.draft.id,
            file=FakeUploadFile("report.pdf", VALID_PDF))
        self.assertEqual(row.scan_status, "clean")
        self.assertIsNotNone(row.storage_key)
        self.assertEqual(row.draft_id, self.draft.id)

        request = outbound_email_service.request_send(self.draft.id,
                                                      connector_id=self.connector.id,
                                                      db=self.db, user=self.user)
        outbound_email_service.decide_request(request.id, approved=True, note=None,
                                              db=self.db, user=self.approver)
        with patch("app.services.notification.outbound_email_service.smtplib.SMTP", FakeSMTP):
            outbound_email_service.execute_request(request.id, db=self.db, user=self.user)
        self.assertEqual(len(FakeSMTP.sent), 1)
        parts = list(FakeSMTP.sent[0].iter_attachments())
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].get_filename(), "report.pdf")

    def test_oversized_attachment_rejected(self):
        with patch.object(get_settings(), "MAILBOX_ATTACHMENT_MAX_BYTES", 256):
            with self.assertRaises(ValueError):
                outbound_email_service.upload_attachment(
                    db=self.db, user=self.user, draft_id=self.draft.id,
                    file=FakeUploadFile("big.pdf", VALID_PDF + b"x" * 1024))
        self.assertEqual(self.db.query(EmailAttachment).count(), 0)

    def test_fake_mime_attachment_rejected(self):
        # .pdf 但内容不是 PDF → 真实 MIME 校验失败
        with self.assertRaises(Exception):
            outbound_email_service.upload_attachment(
                db=self.db, user=self.user, draft_id=self.draft.id,
                file=FakeUploadFile("fake.pdf", b"plain text not a pdf at all"))
        self.assertEqual(self.db.query(EmailAttachment).count(), 0)

    def test_dlp_blocked_attachment_filename_rejected(self):
        with self.assertRaisesRegex(ValueError, "DLP"):
            outbound_email_service.upload_attachment(
                db=self.db, user=self.user, draft_id=self.draft.id,
                file=FakeUploadFile("sk_abcdefghijklmnopqrstuvwxyz123456.pdf", VALID_PDF))
        self.assertEqual(self.db.query(EmailAttachment).count(), 0)

    def test_upload_requires_owned_draft(self):
        other = User(username="other", email="o@example.com",
                     hashed_password=hash_password("pw"), role="user",
                     status=UserStatus.active.value, organization_id=self.org_id)
        self.db.add(other)
        self.db.commit()
        with self.assertRaises(ValueError):
            outbound_email_service.upload_attachment(
                db=self.db, user=other, draft_id=self.draft.id,
                file=FakeUploadFile("report.pdf", VALID_PDF))


if __name__ == "__main__":
    unittest.main()
