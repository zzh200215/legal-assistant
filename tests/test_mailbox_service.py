import json
import unittest
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.connector import MailboxMessage
from app.models.task import Task
from app.models.user import User
from app.schemas.mailbox import ImapMailboxCreateRequest, MailboxTaskConfirmRequest
from app.services.connector_service import connector_service
from app.services.mailbox_service import mailbox_service


def _message_bytes(subject: str, sender: str, body: str) -> bytes:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = "worker@example.com"
    message["Date"] = "Mon, 14 Jul 2026 09:00:00 +0800"
    message["Message-ID"] = f"<{subject}@example.com>"
    message.set_content(body)
    return message.as_bytes()


class FakeImap:
    messages = {
        "1": _message_bytes("Urgent: confirm release scope", "manager@example.com", "Please confirm the release scope today."),
        "2": _message_bytes("Weekly meeting notes", "team@example.com", "Please follow up on the acceptance checklist."),
    }

    def __init__(self, host, port):
        self.host = host
        self.port = port

    def login(self, username, password):
        return "OK", [b"logged in"]

    def select(self, mailbox, readonly=True):
        return "OK", [b"2"]

    def uid(self, command, *args):
        if command == "search":
            return "OK", [b"1 2"]
        if command == "fetch":
            uid = args[0]
            return "OK", [(b"1 (RFC822 {100}", self.messages[uid]), b")"]
        raise AssertionError(f"unexpected IMAP command {command}")

    def logout(self):
        return "BYE", [b"logout"]


class MailboxServiceTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        self.db = SessionLocal()
        self.user = User(username="mail_user", email="mail@example.com", hashed_password="secret")
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)
        self.connector = mailbox_service.create_imap_connector(
            db=self.db,
            user=self.user,
            request=ImapMailboxCreateRequest(
                name="企业邮箱",
                host="imap.example.com",
                username="mail@example.com",
                password="app-password",
                important_senders=["manager@example.com"],
            ),
        )

    def tearDown(self):
        self.db.close()

    def test_credentials_are_encrypted_and_connector_output_is_safe(self):
        self.assertNotIn("app-password", self.connector.credential_ciphertext)
        self.assertEqual(mailbox_service.decrypt_credentials(self.connector.credential_ciphertext)["password"], "app-password")
        payload = connector_service.serialize_connector(self.connector)
        self.assertNotIn("app-password", payload["config_json"] or "")
        self.assertNotIn("credential_ciphertext", payload)

    def test_connector_owner_can_rotate_credentials_without_changing_config(self):
        original_ciphertext = self.connector.credential_ciphertext
        original_config = self.connector.config_json
        rotated = connector_service.rotate_credentials(
            db=self.db,
            connector_id=self.connector.id,
            user=self.user,
            username="rotated@example.com",
            password="new-app-password",
        )
        self.assertNotEqual(rotated.credential_ciphertext, original_ciphertext)
        self.assertEqual(rotated.config_json, original_config)
        self.assertEqual(
            mailbox_service.decrypt_credentials(rotated.credential_ciphertext),
            {"username": "rotated@example.com", "password": "new-app-password"},
        )
        other = User(username="credential_other", email="credential-other@example.com", hashed_password="secret")
        self.db.add(other)
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "Connector not found"):
            connector_service.rotate_credentials(
                db=self.db,
                connector_id=self.connector.id,
                user=other,
                username="other@example.com",
                password="not-allowed",
            )

    def test_connector_disable_revokes_credentials_and_blocks_future_rotation(self):
        disabled = connector_service.disable_connector(db=self.db, connector_id=self.connector.id, user=self.user)
        self.assertEqual(disabled.status, "disabled")
        self.assertIsNone(disabled.credential_ciphertext)
        self.assertIsNone(disabled.sync_cursor_json)
        with self.assertRaises(ValueError):
            mailbox_service.decrypt_credentials(disabled.credential_ciphertext)

    def test_imap_sync_is_incremental_and_task_needs_confirmation(self):
        with patch("app.services.mailbox_service.imaplib.IMAP4_SSL", FakeImap):
            first = mailbox_service.sync_imap_connector(self.connector, db=self.db)
            second = mailbox_service.sync_imap_connector(self.connector, db=self.db)

        self.assertEqual(first["imported_count"], 2)
        self.assertEqual(first["category_counts"]["action"], 2)
        self.assertEqual(second["scanned_count"], 0)
        messages = self.db.query(MailboxMessage).order_by(MailboxMessage.id).all()
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].importance, "high")
        self.assertIsNone(messages[0].task_id)

        suggestion = mailbox_service.task_suggestion(messages[0].id, db=self.db, user=self.user)
        self.assertEqual(suggestion["already_created_task_id"], None)
        task = mailbox_service.confirm_task(
            messages[0].id,
            db=self.db,
            user=self.user,
            request=MailboxTaskConfirmRequest(title="确认上线范围", priority="high"),
        )
        repeated = mailbox_service.confirm_task(
            messages[0].id,
            db=self.db,
            user=self.user,
            request=MailboxTaskConfirmRequest(),
        )
        self.assertEqual(task.id, repeated.id)
        self.assertEqual(task.source_type, "mailbox_email")
        self.assertEqual(task.source_id, messages[0].id)

    def test_top_important_prioritizes_action_messages_from_today(self):
        now = datetime.now(timezone.utc)
        self.db.add_all([
            MailboxMessage(connector_id=self.connector.id, user_id=self.user.id, message_uid="priority-1", mailbox="INBOX", subject="紧急确认", body_text="请今天确认", category="action", importance="high", received_at=now),
            MailboxMessage(connector_id=self.connector.id, user_id=self.user.id, message_uid="priority-2", mailbox="INBOX", subject="订阅", category="subscription", importance="normal", received_at=now),
        ])
        self.db.commit()
        rows = mailbox_service.top_important(db=self.db, user=self.user, limit=2)
        self.assertEqual(rows[0].message_uid, "priority-1")
        self.assertGreater(mailbox_service.priority_score(rows[0]), mailbox_service.priority_score(rows[1]))

    def test_retention_preview_and_purge_protect_task_linked_and_other_user_messages(self):
        old_time = datetime.utcnow() - timedelta(days=120)
        linked_task = Task(user_id=self.user.id, title="保留追溯", status="todo", priority="medium")
        other = User(username="retention_other", email="retention-other@example.com", hashed_password="secret")
        self.db.add_all([linked_task, other])
        self.db.commit()
        self.db.add_all([
            MailboxMessage(connector_id=self.connector.id, user_id=self.user.id, message_uid="old-delete", mailbox="INBOX", subject="可清理", category="other", importance="normal", received_at=old_time),
            MailboxMessage(connector_id=self.connector.id, user_id=self.user.id, message_uid="old-linked", mailbox="INBOX", subject="保留", category="action", importance="normal", received_at=old_time, task_id=linked_task.id),
            MailboxMessage(connector_id=self.connector.id, user_id=other.id, message_uid="old-other", mailbox="INBOX", subject="其他用户", category="other", importance="normal", received_at=old_time),
        ])
        self.db.commit()

        preview = mailbox_service.retention_preview(db=self.db, user=self.user, retention_days=90)
        self.assertEqual(preview["purgeable_count"], 1)
        self.assertEqual(preview["protected_task_linked_count"], 1)
        result = mailbox_service.purge_retained_messages(db=self.db, user=self.user, retention_days=90)
        self.assertEqual(result["deleted_count"], 1)
        self.assertIsNone(self.db.query(MailboxMessage).filter_by(message_uid="old-delete").first())
        self.assertIsNotNone(self.db.query(MailboxMessage).filter_by(message_uid="old-linked").first())
        self.assertIsNotNone(self.db.query(MailboxMessage).filter_by(message_uid="old-other").first())


if __name__ == "__main__":
    unittest.main()
