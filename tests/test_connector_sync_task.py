"""连接器同步框架测试：断点恢复 / cursor 推进 / 增量去重 / 相同 hash 跳过。

验收映射：断点恢复不重复写、批次失败 cursor 不推进、相同对象+hash 不重复。
"""
import json
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  - 注册全部模型到 Base.metadata
from app.core.database import Base
from app.core.time import utc_now
from app.models.connector import ExternalConnector
from app.models.sync_run import SyncRun
from app.services.connector_sync_framework import get_or_create_run, run_sync_run
from app.services.mock_connector_client import MockConnectorClient, MockSink


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _connector(db, conn_id=1):
    c = ExternalConnector(
        id=conn_id, user_id=1, connector_type="mock", name="mock", status="active",
        sync_cursor_json=None,
    )
    db.add(c)
    db.commit()
    return c


def _items(conn_id, n, version_suffix="v1"):
    return [
        {"external_id": f"external-{conn_id}-{i}", "version": f"{version_suffix}-{i}", "data": {"n": i}}
        for i in range(n)
    ]


def _new_run(db, connector):
    run = SyncRun(
        connector_id=connector.id, user_id=1, sync_mode="manual", status="pending",
        idempotency_key=f"sync:{connector.id}:{utc_now().strftime('%Y%m%dT%H%M%S')}",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


class ConnectorSyncFrameworkTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.conn = _connector(self.db)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _run(self, *, items=None, batch_size=2, sink=None, client=None):
        run = _new_run(self.db, self.conn)
        client = client or MockConnectorClient(items=items or _items(self.conn.id, 5))
        sink = sink or MockSink()
        return run, client, sink

    def test_happy_path_advances_cursor(self):
        """快乐路径：全部落地，cursor 推进到最后一个真实游标。"""
        run, client, sink = self._run(batch_size=2)
        res = run_sync_run(db=self.db, run=run, connector=self.conn,
                           client=client, sink=sink, batch_size=2)
        self.assertEqual(res["status"], "succeeded")
        self.assertEqual(res["processed"], 5)
        self.assertEqual(res["succeeded"], 5)
        self.assertEqual(len(sink.seen), 5)
        # 末批 next_cursor=None 时不写入 "null"，保留最后真实游标
        self.assertEqual(json.loads(self.conn.sync_cursor_json), "4")

    def test_succeeded_run_is_idempotent_skip(self):
        run, client, sink = self._run(batch_size=2)
        run_sync_run(db=self.db, run=run, connector=self.conn,
                     client=client, sink=sink, batch_size=2)
        latest = get_or_create_run(self.db, connector=self.conn, owner="x",
                                   sync_mode="manual", ttl_seconds=900)
        self.assertIsNone(latest, "最近一次已成功 → 幂等跳过")

    def test_interrupt_recovery_no_duplicate_writes(self):
        """第 2 页中断 → cursor 停批 1；重跑从已提交 cursor 续，不重复写。"""
        run, client, sink = self._run(
            client=MockConnectorClient(items=_items(self.conn.id, 5), interrupt_after=2))
        with self.assertRaises(RuntimeError):
            run_sync_run(db=self.db, run=run, connector=self.conn,
                         client=client, sink=sink, batch_size=2)
        self.db.refresh(run)
        self.assertEqual(run.status, "failed")
        self.assertEqual(run.attempt, 1)
        self.assertIsNotNone(run.next_retry_at)
        self.assertEqual(json.loads(self.conn.sync_cursor_json), "2", "cursor 停批 1 末")
        self.assertEqual(len(sink.seen), 2)
        # 重跑：复用 failed run，只补批 2、3（3 个新对象）
        client2 = MockConnectorClient(items=_items(self.conn.id, 5))
        sink2 = MockSink()
        res = run_sync_run(db=self.db, run=run, connector=self.conn,
                           client=client2, sink=sink2, batch_size=2)
        self.assertEqual(res["status"], "succeeded")
        self.assertEqual(len(sink2.seen), 3, "断点恢复不重复写已提交对象")
        self.assertEqual(json.loads(self.conn.sync_cursor_json), "4")

    def test_batch_failure_cursor_stops_and_attempt_increments(self):
        """批 2 中 sink 失败 → cursor 停批 1、attempt+1、next_retry_at 置位。"""
        items = _items(self.conn.id, 5)
        run, client, sink = self._run(
            client=MockConnectorClient(items=items),
            sink=MockSink(fail_external_id=items[2]["external_id"]),
        )
        with self.assertRaises(RuntimeError):
            run_sync_run(db=self.db, run=run, connector=self.conn,
                         client=client, sink=sink, batch_size=2)
        self.db.refresh(run)
        self.assertEqual(run.status, "failed")
        self.assertEqual(run.attempt, 1)
        self.assertIsNotNone(run.next_retry_at)
        self.assertEqual(json.loads(self.conn.sync_cursor_json), "2")
        self.assertEqual(len(sink.seen), 2)

    def test_same_object_same_hash_is_skipped(self):
        """相同对象 + hash 不变 → 全部跳过（sink 不重复写、succeeded=0）。"""
        run, client, sink = self._run(
            client=MockConnectorClient(items=_items(self.conn.id, 3)), batch_size=2)
        res = run_sync_run(db=self.db, run=run, connector=self.conn,
                           client=client, sink=sink, batch_size=2)
        self.assertEqual(res["succeeded"], 3)
        first_upserts = sink.upserts
        # 再次全量拉取（模拟游标重置）：已提交对象 hash 未变 → 全部跳过
        self.conn.sync_cursor_json = None
        self.db.commit()
        run2 = _new_run(self.db, self.conn)
        sink2 = MockSink()
        res2 = run_sync_run(db=self.db, run=run2, connector=self.conn,
                            client=MockConnectorClient(items=_items(self.conn.id, 3)),
                            sink=sink2, batch_size=2)
        self.assertEqual(res2["succeeded"], 0)
        self.assertEqual(sink2.upserts, 0)
        self.assertEqual(res2["processed"], 3)
        self.assertEqual(first_upserts, 3)

    def test_connector_sync_task_disabled_by_default(self):
        """connector_sync_task 在 CONNECTOR_SYNC_ENABLED=false 时安全返回 disabled。"""
        import app.tasks as tasks
        from app.core.config import get_settings
        with patch.object(get_settings(), "CONNECTOR_SYNC_ENABLED", False):
            res = tasks.connector_sync_task(1)
        self.assertEqual(res.get("status"), "disabled")


if __name__ == "__main__":
    unittest.main()
