"""归档服务单元测试：默认关闭 / dry-run / 真实删除 / 幂等 / 分批 / 锁 / 时间列差异，
以及内容类保留（邮箱镜像 + 邮件死信，含对象存储 blob 与外键边界）。"""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.database import Base
from app.models.archive import DatabaseArchiveRun
from app.models.email import EmailAttachment, EmailSendRequest
from app.models.legal_notifications import LegalNotificationEvent, SecurityAuditEvent
from app.models.mailbox import MailboxAttachment, MailboxMessage
from app.models.operation_log import OperationLog
from app.services.documents.archive_service import archive_service

OLD = datetime(2020, 1, 1)  # naive UTC，匹配 SQLite 无时区存储
NEW = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=5)


def _make_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _enable_archive(retention_json, *, dry_run=True, batch_size=50):
    settings = get_settings()
    return patch.multiple(
        settings,
        DATABASE_ARCHIVE_ENABLED=True,
        DATABASE_ARCHIVE_DRY_RUN=dry_run,
        DATABASE_ARCHIVE_RETENTION_DAYS_JSON=retention_json,
        DATABASE_ARCHIVE_BATCH_SIZE=batch_size,
    )


class ArchiveServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _seed_operation_logs(self, old_count=3, new_count=2):
        for i in range(old_count):
            self.db.add(OperationLog(module="test", action="old", user_id=1, created_at=OLD))
        for i in range(new_count):
            self.db.add(OperationLog(module="test", action="new", user_id=1, created_at=NEW))
        self.db.commit()
        return old_count + new_count

    def _seeded_log_count(self):
        # 归档审计本身也会写 operation_logs，计数时按种子 action 过滤
        return self.db.query(OperationLog).filter(OperationLog.action.in_(["old", "new"])).count()

    def test_disabled_by_default(self):
        total = self._seed_operation_logs()
        result = archive_service.run(self.db, dry_run=False)
        self.assertFalse(result["enabled"])
        self.assertEqual(self.db.query(OperationLog).count(), total)

    def test_dry_run_counts_without_deleting(self):
        total = self._seed_operation_logs()
        patches = _enable_archive('{"operation_logs": 30}', dry_run=True)
        with patches:
            result = archive_service.run(self.db)
        self.assertEqual(result["tables"]["operation_logs"]["status"], "completed")
        self.assertEqual(result["tables"]["operation_logs"]["processed"], 3)
        self.assertEqual(result["tables"]["operation_logs"]["deleted"], 0)
        self.assertEqual(self._seeded_log_count(), total)  # 未删除

    def test_archive_deletes_old_keeps_new(self):
        total = self._seed_operation_logs()
        with _enable_archive('{"operation_logs": 30}', dry_run=False):
            result = archive_service.run(self.db)
        table_result = result["tables"]["operation_logs"]
        self.assertEqual(table_result["deleted"], 3)
        remaining = self.db.query(OperationLog).filter(OperationLog.action.in_(["old", "new"])).all()
        self.assertEqual(len(remaining), total - 3)
        self.assertTrue(all(r.action == "new" for r in remaining))
        run = self.db.query(DatabaseArchiveRun).filter_by(table_name="operation_logs").first()
        self.assertIsNotNone(run)
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.deleted_count, 3)

    def test_archive_is_idempotent(self):
        self._seed_operation_logs()
        with _enable_archive('{"operation_logs": 30}', dry_run=False):
            archive_service.run(self.db)
            second = archive_service.run(self.db)
        self.assertEqual(second["tables"]["operation_logs"]["deleted"], 0)

    def test_archive_batches_all_rows(self):
        # 超过单批大小：分批游标应删完所有过期行
        self._seed_operation_logs(old_count=12, new_count=1)
        with _enable_archive('{"operation_logs": 30}', dry_run=False, batch_size=5):
            result = archive_service.run(self.db)
        self.assertEqual(result["tables"]["operation_logs"]["deleted"], 12)
        self.assertEqual(self._seeded_log_count(), 1)

    def test_archive_lock_blocks_concurrent_run(self):
        self._seed_operation_logs()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        self.db.add(DatabaseArchiveRun(
            table_name="operation_logs", status="running", dry_run=False,
            cutoff=now, started_at=now,
        ))
        self.db.commit()
        with _enable_archive('{"operation_logs": 30}', dry_run=False):
            result = archive_service.run(self.db)
        self.assertEqual(result["tables"]["operation_logs"]["status"], "skipped_locked")

    def test_stale_lock_is_preempted(self):
        self._seed_operation_logs()
        stale = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
        self.db.add(DatabaseArchiveRun(
            table_name="operation_logs", status="running", dry_run=False,
            cutoff=stale, started_at=stale,
        ))
        self.db.commit()
        with _enable_archive('{"operation_logs": 30}', dry_run=False):
            result = archive_service.run(self.db)
        self.assertEqual(result["tables"]["operation_logs"]["status"], "completed")
        stale_run = self.db.query(DatabaseArchiveRun).filter_by(status="failed").first()
        self.assertIsNotNone(stale_run, "陈旧运行应被标记 failed 并抢占")

    def test_archive_uses_table_specific_time_column(self):
        # security_audit_events 用 occurred_at 而非 created_at；
        # P1 安全策略：审计表默认不物理删除（retained_by_policy），
        # 启用归档 + 受控清理后才删除已归档的过期行。
        import tempfile
        from pathlib import Path

        self.db.add(SecurityAuditEvent(
            organization_id=None, event_type="login", actor_type="user",
            actor_id="1", result="success", occurred_at=OLD, seq_no=1, current_hash="h1",
        ))
        self.db.add(SecurityAuditEvent(
            organization_id=None, event_type="login", actor_type="user",
            actor_id="2", result="success", occurred_at=NEW, seq_no=2, current_hash="h2",
        ))
        self.db.commit()

        settings = get_settings()
        with tempfile.TemporaryDirectory() as tmp:
            with _enable_archive('{"security_audit_events": 365}', dry_run=False), \
                 patch.multiple(settings, OBS_AUDIT_ARCHIVE_DIR=str(Path(tmp) / "archives")):
                result = archive_service.run(self.db)
            self.assertEqual(result["tables"]["security_audit_events"]["status"], "retained_by_policy")
            self.assertEqual(self.db.query(SecurityAuditEvent).count(), 2)  # 审计默认保留不删

            with _enable_archive('{"security_audit_events": 365}', dry_run=False), \
                 patch.multiple(settings, OBS_AUDIT_ARCHIVE_ENABLED=True,
                                OBS_AUDIT_PURGE_AFTER_ARCHIVE=True,
                                OBS_AUDIT_ARCHIVE_DIR=str(Path(tmp) / "archives")):
                result = archive_service.run(self.db)
        # 受控清理按 occurred_at 判定：仅过期行被归档并删除
        # （归档行为本身会写 event_type=export 审计事件，故按业务事件过滤计数）
        self.assertEqual(result["tables"]["security_audit_events"]["deleted"], 1)
        remaining_login = self.db.query(SecurityAuditEvent).filter(
            SecurityAuditEvent.event_type == "login").count()
        self.assertEqual(remaining_login, 1)

    def test_unknown_table_is_skipped(self):
        self._seed_operation_logs()
        with _enable_archive('{"not_a_real_table": 30}', dry_run=False):
            result = archive_service.run(self.db)
        self.assertEqual(result["tables"]["not_a_real_table"]["status"], "unknown_table")


class MailboxRetentionTests(unittest.TestCase):
    """MAILBOX_RETENTION_DAYS 真正执行：过期邮件 + 附件行 + 对象存储 blob。"""

    def setUp(self):
        self.engine = _make_engine()
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _message(self, *, uid, received_at, keys=()):
        msg = MailboxMessage(account_id=1, folder="INBOX", uidvalidity="1", uid=uid,
                             subject=f"mail-{uid}", content_hash=f"h{uid}",
                             received_at=received_at, created_at=received_at)
        self.db.add(msg)
        self.db.commit()
        self.db.refresh(msg)
        for idx, key in enumerate(keys):
            self.db.add(MailboxAttachment(message_id=msg.id, account_id=1,
                                          filename=f"a{idx}.pdf", content_hash=key,
                                          storage_key=key))
        self.db.commit()
        return msg

    def test_dry_run_counts_without_deleting(self):
        self._message(uid="1", received_at=OLD, keys=["blob-old"])
        with _enable_archive("{}", dry_run=True), \
             patch("app.services.storage.storage_service.storage_service.delete") as blob_delete:
            result = archive_service.run(self.db)["tables"]["mailbox"]
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["deleted"], 0)
        blob_delete.assert_not_called()
        self.assertEqual(self.db.query(MailboxMessage).count(), 1)
        self.assertEqual(self.db.query(MailboxAttachment).count(), 1)

    def test_expired_messages_attachments_and_blobs_deleted(self):
        self._message(uid="1", received_at=OLD, keys=["blob-old"])
        self._message(uid="2", received_at=NEW, keys=["blob-new"])
        with _enable_archive("{}", dry_run=False), \
             patch("app.services.storage.storage_service.storage_service.delete") as blob_delete:
            result = archive_service.run(self.db)["tables"]["mailbox"]
        self.assertEqual((result["deleted"], result["attachments_deleted"]), (1, 1))
        self.assertEqual(result["blobs_deleted"], 1)
        blob_delete.assert_called_once_with("blob-old")
        remaining = self.db.query(MailboxMessage).all()
        self.assertEqual([m.uid for m in remaining], ["2"])
        self.assertEqual(self.db.query(MailboxAttachment).count(), 1)
        run = self.db.query(DatabaseArchiveRun).filter_by(table_name="mailbox_messages").first()
        self.assertEqual((run.status, run.deleted_count), ("completed", 1))

    def test_blob_shared_with_live_attachment_is_kept(self):
        """附件按内容哈希去重：同一 storage_key 仍被未过期邮件引用时不能删 blob。"""
        self._message(uid="1", received_at=OLD, keys=["shared-blob"])
        self._message(uid="2", received_at=NEW, keys=["shared-blob"])
        with _enable_archive("{}", dry_run=False), \
             patch("app.services.storage.storage_service.storage_service.delete") as blob_delete:
            result = archive_service.run(self.db)["tables"]["mailbox"]
        self.assertEqual(result["deleted"], 1)
        self.assertEqual(result["blobs_deleted"], 0)
        blob_delete.assert_not_called()

    def test_cleanup_is_idempotent_and_batched(self):
        for i in range(7):
            self._message(uid=f"old-{i}", received_at=OLD, keys=[f"blob-{i}"])
        with _enable_archive("{}", dry_run=False, batch_size=2), \
             patch("app.services.storage.storage_service.storage_service.delete"):
            first = archive_service.run(self.db)["tables"]["mailbox"]
            second = archive_service.run(self.db)["tables"]["mailbox"]
        self.assertEqual(first["deleted"], 7)
        self.assertEqual(second["deleted"], 0)
        self.assertEqual(self.db.query(MailboxMessage).count(), 0)
        self.assertEqual(self.db.query(MailboxAttachment).count(), 0)

    def test_blob_failure_does_not_abort_run(self):
        self._message(uid="1", received_at=OLD, keys=["blob-old"])
        with _enable_archive("{}", dry_run=False), \
             patch("app.services.storage.storage_service.storage_service.delete",
                   side_effect=OSError("object store down")):
            result = archive_service.run(self.db)["tables"]["mailbox"]
        self.assertEqual(result["status"], "completed")
        self.assertEqual((result["deleted"], result["blob_failures"]), (1, 1))


class EmailDeadLetterRetentionTests(unittest.TestCase):
    """EMAIL_DEAD_LETTER_RETENTION_DAYS 真正执行，且不破坏外键与用户内容。"""

    def setUp(self):
        self.engine = _make_engine()
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _request(self, *, key, status="dead_letter", dead_letter_at=OLD):
        row = EmailSendRequest(
            draft_id=1, smtp_connector_id=1, user_id=1, recipient="a@example.com",
            subject="s", content_hash="c", idempotency_key=key, status=status,
            dead_letter_at=dead_letter_at, created_at=dead_letter_at or OLD,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def test_expired_dead_letter_deleted_and_attachment_unlinked(self):
        expired = self._request(key="k-old")
        self.db.add(EmailAttachment(draft_id=1, send_request_id=expired.id,
                                    filename="a.pdf", content_hash="h", storage_key="blob"))
        self.db.commit()
        with _enable_archive("{}", dry_run=False):
            result = archive_service.run(self.db)["tables"]["email_dead_letters"]
        self.assertEqual((result["deleted"], result["attachments_unlinked"]), (1, 1))
        self.assertEqual(self.db.query(EmailSendRequest).count(), 0)
        # 附件行与 blob 属用户内容（草稿仍在），只解除引用
        attachment = self.db.query(EmailAttachment).one()
        self.assertIsNone(attachment.send_request_id)
        self.assertEqual(attachment.storage_key, "blob")

    def test_non_dead_letter_and_recent_dead_letter_are_kept(self):
        self._request(key="k-sent", status="sent")
        self._request(key="k-fresh", dead_letter_at=NEW)
        with _enable_archive("{}", dry_run=False):
            result = archive_service.run(self.db)["tables"]["email_dead_letters"]
        self.assertEqual((result["processed"], result["deleted"]), (0, 0))
        self.assertEqual(self.db.query(EmailSendRequest).count(), 2)

    def test_dead_letter_referenced_by_notification_event_is_skipped(self):
        referenced = self._request(key="k-linked")
        self.db.add(LegalNotificationEvent(
            organization_id=1, user_id=1, event_type="deadline", title="t",
            channel="email", status="dead_letter", email_send_request_id=referenced.id,
        ))
        self.db.commit()
        with _enable_archive("{}", dry_run=False):
            result = archive_service.run(self.db)["tables"]["email_dead_letters"]
        self.assertEqual((result["deleted"], result["skipped_referenced"]), (0, 1))
        self.assertEqual(self.db.query(EmailSendRequest).count(), 1)


if __name__ == "__main__":
    unittest.main()
