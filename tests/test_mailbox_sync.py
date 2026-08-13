"""邮箱同步测试：UID 幂等、checkpoint 断点恢复、cursor 不提前推进、附件安全。"""
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.config import get_settings
from app.core.database import Base
from app.models.mailbox import MailboxAttachment, MailboxMessage, MailboxSyncAccount
from app.services.mailbox_sync_service import (
    MockMailboxClient, build_mock_mailbox, mailbox_sync_service,
)


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


class MailboxSyncTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)()
        self.account = MailboxSyncAccount(connector_id=1, user_id=1,
                                          email_address="demo@example.com", status="active")
        self.db.add(self.account)
        self.db.commit()
        self.db.refresh(self.account)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_uid_idempotency_full_rescan(self):
        client = MockMailboxClient(build_mock_mailbox(self.account.id, count=6))
        r1 = mailbox_sync_service.sync_account(db=self.db, account=self.account,
                                               owner="t1", batch_size=2, client=client)
        self.assertEqual(r1["status"], "succeeded")
        self.assertEqual(self.db.query(MailboxMessage).count(), 6)
        self.assertEqual(self.db.query(MailboxAttachment).count(), 6)
        self.assertEqual(self.account.last_successful_uid, "6")
        # 重置游标模拟全量重扫：同 UID 幂等，不重复创建
        self.account.cursor_json = None
        self.db.commit()
        client2 = MockMailboxClient(build_mock_mailbox(self.account.id, count=6))
        r2 = mailbox_sync_service.sync_account(db=self.db, account=self.account,
                                               owner="t2", batch_size=2, client=client2)
        self.assertEqual(r2["succeeded"], 0, "同 UIDVALIDITY+UID 重复同步不重复创建")
        self.assertEqual(self.db.query(MailboxMessage).count(), 6)
        self.assertEqual(self.db.query(MailboxAttachment).count(), 6)

    def test_interrupt_recovery_from_checkpoint(self):
        client = MockMailboxClient(build_mock_mailbox(self.account.id, count=6), interrupt_after=2)
        with self.assertRaises(RuntimeError):
            mailbox_sync_service.sync_account(db=self.db, account=self.account,
                                              owner="t1", batch_size=2, client=client)
        self.db.refresh(self.account)
        self.assertEqual(self.account.status, "error")
        self.assertIsNotNone(self.account.next_retry_at)
        self.assertEqual(self.account.cursor_json, '"2"', "批次失败不推进 cursor")
        self.assertEqual(self.db.query(MailboxMessage).count(), 2)
        # 断点恢复：从 checkpoint 续，只补剩余，不重复写
        client2 = MockMailboxClient(build_mock_mailbox(self.account.id, count=6))
        r2 = mailbox_sync_service.sync_account(db=self.db, account=self.account,
                                               owner="t2", batch_size=2, client=client2)
        self.assertEqual(r2["status"], "succeeded")
        self.assertEqual(self.db.query(MailboxMessage).count(), 6)
        self.assertEqual(self.account.cursor_json, '"4"')

    def test_cursor_advances_only_after_whole_batch_commits(self):
        # interrupt_after=1：第一页成功（cursor=2），第二页中断 → cursor 停批 1
        client = MockMailboxClient(build_mock_mailbox(self.account.id, count=6), interrupt_after=2)
        with self.assertRaises(RuntimeError):
            mailbox_sync_service.sync_account(db=self.db, account=self.account,
                                              owner="t1", batch_size=2, client=client)
        self.db.refresh(self.account)
        self.assertEqual(self.account.cursor_json, '"2"')
        self.assertEqual(self.db.query(MailboxMessage).count(), 2)

    def test_oversized_attachment_is_blocked_and_quarantined(self):
        messages = build_mock_mailbox(self.account.id, count=1)
        messages[0]["attachments"] = [{
            "filename": "big.pdf", "mime_type": "application/pdf",
            "content": b"x" * 2048,
        }]
        with patch.object(get_settings(), "MAILBOX_ATTACHMENT_MAX_BYTES", 1024):
            r = mailbox_sync_service.sync_account(db=self.db, account=self.account,
                                                  owner="t1", batch_size=2,
                                                  client=MockMailboxClient(messages))
        self.assertEqual(r["status"], "succeeded")
        msg = self.db.query(MailboxMessage).first()
        self.assertEqual(msg.process_result, "quarantined")
        att = self.db.query(MailboxAttachment).first()
        self.assertEqual(att.scan_status, "blocked")
        self.assertIn("too_large", att.scan_result_json)

    def test_fake_mime_attachment_blocked(self):
        messages = build_mock_mailbox(self.account.id, count=1)
        messages[0]["attachments"] = [{
            "filename": "fake.pdf", "mime_type": "application/pdf",
            "content": b"plain text that is not a real pdf file",
        }]
        r = mailbox_sync_service.sync_account(db=self.db, account=self.account,
                                              owner="t1", batch_size=2,
                                              client=MockMailboxClient(messages))
        self.assertEqual(r["status"], "succeeded")
        msg = self.db.query(MailboxMessage).first()
        self.assertEqual(msg.process_result, "quarantined")
        att = self.db.query(MailboxAttachment).first()
        self.assertEqual(att.scan_status, "blocked")

    def test_clean_pdf_attachment_imported(self):
        messages = build_mock_mailbox(self.account.id, count=1)
        r = mailbox_sync_service.sync_account(db=self.db, account=self.account,
                                              owner="t1", batch_size=2,
                                              client=MockMailboxClient(messages))
        self.assertEqual(r["status"], "succeeded")
        msg = self.db.query(MailboxMessage).first()
        self.assertEqual(msg.process_result, "success")
        att = self.db.query(MailboxAttachment).first()
        self.assertEqual(att.scan_status, "clean")
        self.assertIsNotNone(att.storage_key)


if __name__ == "__main__":
    unittest.main()
