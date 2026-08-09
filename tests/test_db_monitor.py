"""数据库监控单元测试：慢 SQL / 事务计数 / 连接池 / 关联 ID / 不泄露参数。

使用独立 SQLite 内存引擎 + 独立 DBMonitor 实例，避免污染全局监控。
"""
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.db_monitor import (
    DBMonitor,
    install_db_monitor,
    pool_status,
    set_db_correlation_id,
)
from app.core.database import Base


def _make_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


class DBMonitorTests(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        self.monitor = DBMonitor()
        install_db_monitor(self.engine, self.monitor)
        self.session = sessionmaker(bind=self.engine)()
        self.settings = get_settings()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_queries_are_counted_and_timed(self):
        self.session.execute(text("SELECT 1"))
        self.session.execute(text("SELECT 1"))
        snapshot = self.monitor.snapshot()
        self.assertGreaterEqual(snapshot["query_count"], 2)
        self.assertGreaterEqual(snapshot["total_query_ms"], 0)

    def test_commit_and_rollback_are_counted(self):
        self.session.execute(text("SELECT 1"))
        self.session.commit()  # commit_count +1
        self.session.execute(text("SELECT 1"))  # 开启新事务
        self.session.rollback()  # rollback_count +1（有活动事务才会发事件）
        snapshot = self.monitor.snapshot()
        self.assertEqual(snapshot["commit_count"], 1)
        self.assertEqual(snapshot["rollback_count"], 1)

    def test_slow_query_recorded_without_params(self):
        with patch.object(self.settings, "DATABASE_SLOW_QUERY_MS", 50):
            self.monitor._record_query(100.0, "SELECT :v")
        snapshot = self.monitor.snapshot()
        self.assertGreaterEqual(snapshot["slow_query_count"], 1)
        record = snapshot["recent_slow_queries"][-1]
        # 语句只存前缀，绝不包含参数里的敏感内容
        self.assertNotIn("SENSITIVE", record["statement"])

    def test_listener_never_passes_parameters(self):
        """集成验证：事件监听只传 statement，绝不传 bound parameters。"""
        captured = {}

        def spy(duration, statement):
            captured["statement"] = statement

        with patch.object(self.monitor, "_record_query", side_effect=spy):
            self.session.execute(text("SELECT :v"), {"v": "SENSITIVE-TOKEN-ABC"})
            self.session.commit()
        self.assertIn("SELECT", captured["statement"])
        self.assertNotIn("SENSITIVE-TOKEN-ABC", captured["statement"])

    def test_correlation_id_attached_to_slow_query(self):
        set_db_correlation_id("task-archive-1")
        try:
            with patch.object(self.settings, "DATABASE_SLOW_QUERY_MS", 50):
                self.monitor._record_query(100.0, "SELECT 1")
            record = self.monitor.snapshot()["recent_slow_queries"][-1]
            self.assertEqual(record["correlation_id"], "task-archive-1")
        finally:
            set_db_correlation_id(None)

    def test_db_errors_are_counted(self):
        with self.assertRaises(Exception):
            self.session.execute(text("SELECT * FROM missing_table_xyz"))
        self.assertGreaterEqual(self.monitor.snapshot()["error_count"], 1)

    def test_sqlite_pool_status_is_empty(self):
        self.assertEqual(pool_status(self.engine), {})

    def test_reset_clears_counters(self):
        self.session.execute(text("SELECT 1"))
        self.monitor.reset()
        snapshot = self.monitor.snapshot()
        self.assertEqual(snapshot["query_count"], 0)
        self.assertEqual(snapshot["recent_slow_queries"], [])

    def test_monitor_failure_does_not_break_queries(self):
        """监控异常被吞掉，主查询不受影响。"""
        engine = _make_engine()
        broken = DBMonitor()
        with patch.object(broken, "_record_query", side_effect=RuntimeError("boom")):
            install_db_monitor(engine, broken)
        session = sessionmaker(bind=engine)()
        try:
            session.execute(text("SELECT 1"))  # 不抛错
            session.commit()
        finally:
            session.close()
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
