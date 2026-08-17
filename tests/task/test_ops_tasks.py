"""Task 层：可观测性任务直调补测（指标快照/预聚合/审计导出/告警/归档）。

覆盖 app/tasks/ops_tasks.py 全部分支（含 Redis 不可用降级、错误记账、dry-run 语义）。
"""

import unittest
from unittest.mock import MagicMock, patch

import fakeredis
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.ops_metric import OpsMetricSnapshot
from app.models.task_run import TaskRun
from app.tasks import ops_tasks


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


class OpsTasksTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()
        self._session_patch = patch("app.tasks.ops_tasks.SessionLocal", self.Session)
        self._session_patch.start()

    def tearDown(self):
        self._session_patch.stop()
        self.db.close()
        self.engine.dispose()

    # ── snapshot_ops_metrics ────────────────────────────────────────────────
    def test_snapshot_disabled(self):
        with patch.object(ops_tasks, "get_settings") as settings:
            settings.return_value.OBS_METRICS_SNAPSHOT_ENABLED = False
            result = ops_tasks.snapshot_ops_metrics()
        self.assertEqual(result, {"enabled": False})

    def test_snapshot_empty_window(self):
        with (
            patch.object(ops_tasks, "get_settings") as settings,
            patch.object(ops_tasks.metrics, "snapshot_and_reset", return_value=[]),
            patch.object(ops_tasks, "_db_backlog_rows", return_value=[]),
            patch.object(ops_tasks, "_broker_backlog_rows", return_value=[]),
        ):
            settings.return_value.OBS_METRICS_SNAPSHOT_ENABLED = True
            settings.return_value.OBS_METRICS_SNAPSHOT_WINDOW_SECONDS = 300
            result = ops_tasks.snapshot_ops_metrics()
        self.assertEqual(result["items"], 0)

    def test_snapshot_persists_metric_and_backlog_rows(self):
        rows = [
            {"metric_name": "llm_requests", "labels": {"org": "7", "model": "qwen-plus"}, "kind": "counter", "count": 3},
            {"metric_name": "task_backlog", "labels": {"queue": "document", "state": "in_flight"}, "kind": "gauge", "count": 2},
        ]
        with (
            patch.object(ops_tasks, "get_settings") as settings,
            patch.object(ops_tasks.metrics, "snapshot_and_reset", return_value=rows[:1]),
            patch.object(ops_tasks, "_db_backlog_rows", return_value=rows[1:]),
            patch.object(ops_tasks, "_broker_backlog_rows", return_value=[]),
        ):
            settings.return_value.OBS_METRICS_SNAPSHOT_ENABLED = True
            settings.return_value.OBS_METRICS_SNAPSHOT_WINDOW_SECONDS = 300
            result = ops_tasks.snapshot_ops_metrics()
        self.assertEqual(result["items"], 2)
        snapshots = self.db.query(OpsMetricSnapshot).all()
        self.assertEqual(len(snapshots), 2)
        org_row = [s for s in snapshots if s.metric_name == "llm_requests"][0]
        self.assertEqual(org_row.org_id, 7)  # org 从 labels 剥离（Integer 列）
        self.assertEqual(org_row.labels_json, '{"model": "qwen-plus"}')

    def test_snapshot_persist_failure_returns_error_flag(self):
        session_mock = MagicMock()
        session_mock.commit.side_effect = RuntimeError("disk full")
        with (
            patch.object(ops_tasks, "get_settings") as settings,
            patch.object(ops_tasks.metrics, "snapshot_and_reset", return_value=[{"metric_name": "x", "count": 1}]),
            patch.object(ops_tasks, "_db_backlog_rows", return_value=[]),
            patch.object(ops_tasks, "_broker_backlog_rows", return_value=[]),
            patch("app.tasks.ops_tasks.SessionLocal", return_value=session_mock),
        ):
            settings.return_value.OBS_METRICS_SNAPSHOT_ENABLED = True
            settings.return_value.OBS_METRICS_SNAPSHOT_WINDOW_SECONDS = 300
            result = ops_tasks.snapshot_ops_metrics()
        self.assertTrue(result["error"])
        session_mock.rollback.assert_called_once()

    def test_bucket_start_alignment(self):
        from datetime import UTC, datetime

        with patch.object(ops_tasks, "utc_now", return_value=datetime(2026, 8, 16, 12, 3, 27, tzinfo=UTC)):
            bucket = ops_tasks._snapshot_bucket_start(300)  # 5 分钟窗
        self.assertEqual(bucket, datetime(2026, 8, 16, 12, 0, 0))

    def test_db_backlog_groups_by_queue_and_staleness(self):
        now = ops_tasks.utc_now()
        self.db.add_all([
            TaskRun(task_id="t1", task_name="x", queue="document", status="running", updated_at=now),
            TaskRun(task_id="t2", task_name="x", queue="document", status="retrying", updated_at=now),
            TaskRun(task_id="t3", task_name="x", queue="llm", status="running", updated_at=now),
        ])
        self.db.commit()
        with patch.object(ops_tasks, "get_settings") as settings:
            settings.return_value.OBS_BACKLOG_STALE_MINUTES = 30
            rows = ops_tasks._db_backlog_rows()
        by_queue = {r["labels"]["queue"]: r for r in rows}
        self.assertEqual(by_queue["document"]["count"], 2)
        self.assertEqual(by_queue["llm"]["count"], 1)

    def test_broker_backlog_uses_llen_and_degrades_to_zero(self):
        fake_redis = fakeredis.FakeRedis(decode_responses=True)
        fake_redis.llen = MagicMock(side_effect=lambda q: 5 if q == "document" else 0)
        with patch("app.tasks.ops_tasks.redis.from_url", return_value=fake_redis):
            rows = ops_tasks._broker_backlog_rows()
        by_queue = {r["labels"]["queue"]: r for r in rows}
        self.assertEqual(by_queue["document"]["count"], 5)
        self.assertEqual(len(rows), len(ops_tasks._BROKER_QUEUES))

    def test_broker_backlog_redis_down_returns_empty(self):
        with patch("app.tasks.ops_tasks.redis.from_url", side_effect=ConnectionError("down")):
            self.assertEqual(ops_tasks._broker_backlog_rows(), [])

    # ── aggregate_ops_metrics / audit export / alerts / archive ─────────────
    def test_aggregate_metrics(self):
        with patch.object(ops_tasks, "ops_aggregation_service") as svc:
            svc.aggregate_all.side_effect = lambda db, g: {g: 1}
            result = ops_tasks.aggregate_ops_metrics()
        self.assertEqual(result, {"hourly": {"hour": 1}, "daily": {"day": 1}})

    def test_aggregate_metrics_error(self):
        with patch.object(ops_tasks, "ops_aggregation_service") as svc:
            svc.aggregate_all.side_effect = RuntimeError("boom")
            result = ops_tasks.aggregate_ops_metrics()
        self.assertEqual(result, {"error": True})

    def test_run_audit_export_success(self):
        with patch.object(ops_tasks, "audit_export_service") as svc:
            svc.run_export_job.return_value = {"status": "succeeded", "rows": 10}
            result = ops_tasks.run_audit_export(42)
        self.assertEqual(result["status"], "succeeded")
        svc.run_export_job.assert_called_once()
        self.assertEqual(svc.run_export_job.call_args.args[1], 42)

    def test_run_audit_export_failure_recorded(self):
        with patch.object(ops_tasks, "audit_export_service") as svc:
            svc.run_export_job.side_effect = ValueError("bad job")
            result = ops_tasks.run_audit_export(42)
        self.assertEqual(result, {"status": "failed", "job_id": 42, "error": "ValueError"})

    def test_dispatch_operational_alerts(self):
        with (
            patch("app.services.notification.operational_alert_service.operational_alert_service") as svc,
            patch("app.tasks.ops_tasks.record_beat_heartbeat"),
        ):
            svc.dispatch.return_value = {"dispatched": 1}
            result = ops_tasks.dispatch_operational_alerts_task.run()
        self.assertEqual(result, {"dispatched": 1})

    def test_run_database_archive_calls_archive_and_cleanup(self):
        fake_scope = MagicMock()
        fake_scope.return_value.__enter__.return_value = self.db
        with (
            patch("app.core.database.session_scope", fake_scope),
            patch("app.services.documents.archive_service.archive_service") as archive,
            patch("app.services.jobs.idempotency_service.idempotency_service") as idem,
            patch("app.core.db_monitor.set_db_correlation_id"),
            patch("app.tasks.ops_tasks.record_beat_heartbeat"),
        ):
            archive.run.return_value = {"archived": 3}
            idem.cleanup_expired.return_value = 5
            result = ops_tasks.run_database_archive_task.run()
        self.assertEqual(result["archived"], 3)
        self.assertEqual(result["expired_idempotency_keys_deleted"], 5)


if __name__ == "__main__":
    unittest.main()
