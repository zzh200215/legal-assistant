"""任务运行台账测试：创建 / 状态推进 / 失败重试字段保留 / 取代跳过 / 租户过滤。"""
import unittest
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.core.time import utc_now
from app.services.task_run_service import task_run_service


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


class TaskRunServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_start_creates_running_run(self):
        row = task_run_service.start(
            self.db, task_id="t-1", task_name="parse_document", scope="document",
            queue="document", business_key="doc:1",
        )
        self.assertEqual(row.status, "running")
        self.assertEqual(row.attempt, 1)
        self.assertEqual(row.scope, "document")

    def test_retry_reuses_task_id_and_increments_attempt(self):
        row = task_run_service.start(
            self.db, task_id="t-2", task_name="connector_sync_task",
            business_key="connector:1",
        )
        self.assertEqual(row.attempt, 1)
        row = task_run_service.start(
            self.db, task_id="t-2", task_name="connector_sync_task",
            business_key="connector:1", attempt=2,
        )
        self.assertEqual(row.attempt, 2, "Celery 重试复用 task_id，attempt 递增")
        # 只保留一行（多 run 台账：同一 task_id 是同一逻辑 run 的重试）
        self.assertEqual(self.db.query(type(row)).count(), 1)

    def test_failed_keeps_error_fields_for_restart(self):
        """验收 #11：失败 + 重启后 error_code/attempt/checkpoint/next_retry_at 保留。"""
        task_id = "t-3"
        task_run_service.start(self.db, task_id=task_id, task_name="document_index",
                               business_key="doc:9", attempt=1)
        task_run_service.update_checkpoint(self.db, task_id=task_id, checkpoint_json='{"page": 3}')
        retry_at = utc_now() + timedelta(minutes=5)
        task_run_service.mark_failed(
            self.db, task_id=task_id, error_code="TimeoutError",
            error_message="timed out after 540s", attempt=1, next_retry_at=retry_at,
        )
        # 重启/重试 → start 复用行
        task_run_service.start(self.db, task_id=task_id, task_name="document_index",
                               business_key="doc:9", attempt=2)
        task_run_service.update_checkpoint(self.db, task_id=task_id, checkpoint_json='{"page": 6}')
        # 失败字段保留（start 只清 error 但重试后写入新 error）
        task_run_service.mark_failed(
            self.db, task_id=task_id, error_code="TimeoutError",
            error_message="timed out again", attempt=2, next_retry_at=retry_at,
        )
        from app.models.task_run import TaskRun

        row = self.db.query(TaskRun).filter(TaskRun.task_id == task_id).first()
        self.assertEqual(row.status, "failed")
        self.assertEqual(row.error_code, "TimeoutError")
        self.assertEqual(row.attempt, 2)
        self.assertEqual(row.checkpoint_json, '{"page": 6}')
        self.assertIsNotNone(row.next_retry_at)
        self.assertIsNotNone(row.started_at)
        self.assertIsNotNone(row.finished_at)

    def test_error_message_is_sanitized_and_truncated(self):
        from app.models.task_run import TaskRun

        task_id = "t-4"
        task_run_service.start(self.db, task_id=task_id, task_name="summarize_document")
        long_msg = "boom" * 2000  # 8000 字符 → 截断
        task_run_service.mark_failed(self.db, task_id=task_id, error_message=long_msg)
        row = self.db.query(TaskRun).filter(TaskRun.task_id == task_id).first()
        self.assertLessEqual(len(row.error_message or ""), 2000)

    def test_cancelled_or_superseded(self):
        task_id = "t-5"
        task_run_service.start(self.db, task_id=task_id, task_name="document_chunk",
                               business_key="doc:2")
        task_run_service.mark_succeeded(self.db, task_id=task_id)
        self.assertTrue(
            task_run_service.cancelled_or_superseded(
                self.db, task_name="document_chunk", business_key="doc:2"),
            "同业务键最新已成功 → 旧重试应跳过",
        )
        # 另一业务键不受影响
        self.assertFalse(
            task_run_service.cancelled_or_superseded(
                self.db, task_name="document_chunk", business_key="doc:3"))

    def test_list_for_tenant_scoped_and_status_filtered(self):
        from app.models.task_run import TaskRun

        for i in range(3):
            self.db.add(TaskRun(
                task_id=f"t6-{i}", task_name="analyze_document", scope="task",
                tenant_id=10, business_key=f"doc:{i}", status="running",
                started_at=utc_now(), trace_id=f"t6-{i}",
            ))
        self.db.add(TaskRun(
            task_id="t6-x", task_name="analyze_document", scope="task",
            tenant_id=20, business_key="doc:x", status="running",
            started_at=utc_now(), trace_id="t6-x",
        ))
        self.db.commit()
        rows = task_run_service.list_for_tenant(self.db, tenant_id=10)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r.tenant_id == 10 for r in rows))
        self.assertEqual(len(task_run_service.list_for_tenant(self.db, tenant_id=99)), 0)

    def test_cleanup_expired_removes_old_runs(self):
        from app.models.task_run import TaskRun

        old = utc_now() - timedelta(days=60)
        for i in range(2):
            self.db.add(TaskRun(task_id=f"t7-{i}", task_name="run_database_archive",
                                status="succeeded", created_at=old, started_at=old,
                                finished_at=old))
        self.db.add(TaskRun(task_id="t7-new", task_name="run_database_archive",
                            status="succeeded", created_at=utc_now(),
                            started_at=utc_now(), finished_at=utc_now()))
        self.db.commit()
        deleted = task_run_service.cleanup_expired(self.db)
        self.assertEqual(deleted, 2)
        self.assertEqual(self.db.query(TaskRun).count(), 1)


if __name__ == "__main__":
    unittest.main()
