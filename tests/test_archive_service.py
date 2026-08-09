"""归档服务单元测试：默认关闭 / dry-run / 真实删除 / 幂等 / 分批 / 锁 / 时间列差异。"""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.database import Base
from app.models.archive import DatabaseArchiveRun
from app.models.legal_notifications import SecurityAuditEvent
from app.models.operation_log import OperationLog
from app.services.archive_service import archive_service

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
        # security_audit_events 用 occurred_at 而非 created_at
        self.db.add(SecurityAuditEvent(
            organization_id=None, event_type="login", actor_type="user",
            actor_id="1", result="success", occurred_at=OLD, seq_no=1, current_hash="h1",
        ))
        self.db.add(SecurityAuditEvent(
            organization_id=None, event_type="login", actor_type="user",
            actor_id="2", result="success", occurred_at=NEW, seq_no=2, current_hash="h2",
        ))
        self.db.commit()
        with _enable_archive('{"security_audit_events": 365}', dry_run=False):
            result = archive_service.run(self.db)
        self.assertEqual(result["tables"]["security_audit_events"]["deleted"], 1)
        self.assertEqual(self.db.query(SecurityAuditEvent).count(), 1)

    def test_unknown_table_is_skipped(self):
        self._seed_operation_logs()
        with _enable_archive('{"not_a_real_table": 30}', dry_run=False):
            result = archive_service.run(self.db)
        self.assertEqual(result["tables"]["not_a_real_table"]["status"], "unknown_table")


if __name__ == "__main__":
    unittest.main()
