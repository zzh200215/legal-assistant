"""Task/Service 层：文档锁兼容层（token CAS）与 task_run_service 台账补测。

覆盖：
- app/tasks/runtime.py：acquire/release_document_lock 的 token 台账与 CAS 释放、
  Redis 不可用放行、未获锁返回 False；
- app/services/jobs/task_run_service.py：start/update_checkpoint/mark_succeeded 缺失、
  cleanup_expired、get_latest_by_business_key、cancelled_or_superseded。
"""

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.task_run import TaskRun
from app.services.jobs.task_run_service import task_run_service
from app.tasks import runtime


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


class _CasLuaRedis:
    """最小 Lua 语义模拟（同 tests/contract/test_redis_contract.py 的 stub）。"""

    def __init__(self):
        self.store = {}

    def set(self, name, value, nx=False, ex=None):
        if nx and name in self.store:
            return None
        self.store[name] = value
        return True

    def get(self, name):
        return self.store.get(name)

    def eval(self, script, numkeys, *args):
        key, token = args[0], args[1]
        if self.store.get(key) == token:
            self.store.pop(key, None)
            return 1
        return 0


class DocumentLockCompatibilityTests(unittest.TestCase):
    def setUp(self):
        runtime._DOC_LOCK_TOKENS.clear()
        self._patchers = [patch("app.tasks.runtime.redis.from_url", side_effect=RuntimeError("no redis"))]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in reversed(self._patchers):
            p.stop()
        runtime._DOC_LOCK_TOKENS.clear()

    def test_acquire_registers_token_and_returns_true(self):
        r = _CasLuaRedis()
        ok = runtime.acquire_document_lock(1, 60, redis_client=r)
        self.assertTrue(ok)
        self.assertIn(1, runtime._DOC_LOCK_TOKENS)
        # 再次获取（锁被持有）→ False
        self.assertFalse(runtime.acquire_document_lock(1, 60, redis_client=r))

    def test_release_cas_deletes_only_own_token(self):
        r = _CasLuaRedis()
        runtime.acquire_document_lock(2, 60, redis_client=r)
        runtime.release_document_lock(2, redis_client=r)  # 正确 token CAS 释放
        self.assertNotIn(2, runtime._DOC_LOCK_TOKENS)
        self.assertEqual(r.store, {})

    def test_release_without_token_noop(self):
        runtime.release_document_lock(999, redis_client=None)  # 无 token 台账 → 不抛

    def test_redis_unavailable_fails_open(self):
        self.assertTrue(runtime.acquire_document_lock(7, 60))  # Redis 不可用放行


class TaskRunServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _run(self, **kw) -> TaskRun:
        fields = {
            "task_id": "task-1", "task_name": "parse_document", "status": "running",
            "business_key": "document:1", "queue": "document",
        }
        fields.update(kw)
        run = TaskRun(**fields)
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def test_mark_succeeded_missing_task_noop(self):
        self.assertIsNone(task_run_service.mark_succeeded(self.db, task_id="no-such"))

    def test_update_checkpoint(self):
        run = self._run()
        task_run_service.update_checkpoint(self.db, task_id="task-1", checkpoint_json='{"phase": 2}')
        self.db.refresh(run)
        self.assertEqual(run.checkpoint_json, '{"phase": 2}')

    def test_get_latest_by_business_key(self):
        self._run(task_id="task-a", business_key="document:1", status="succeeded")
        self._run(task_id="task-b", business_key="document:1", status="running")
        latest = task_run_service.get_latest_by_business_key(
            self.db, task_name="parse_document", business_key="document:1")
        self.assertEqual(latest.task_id, "task-b")

    def test_cancelled_or_superseded(self):
        # 同业务键最新一次已成功 → 旧重试应跳过（superseded）
        self._run(task_id="task-c", business_key="document:2", status="succeeded")
        self.assertTrue(task_run_service.cancelled_or_superseded(
            self.db, task_name="parse_document", business_key="document:2"))
        # 更晚出现 repeating/cancelled 运行 → 不再视为已成功
        self._run(task_id="task-d", business_key="document:2", status="cancelled")
        self.assertFalse(task_run_service.cancelled_or_superseded(
            self.db, task_name="parse_document", business_key="document:2"))

    def test_cleanup_expired(self):
        from datetime import timedelta

        from app.core.time import utc_now

        self._run(task_id="task-old", status="succeeded",
                  created_at=utc_now() - timedelta(days=400))
        with patch("app.services.jobs.task_run_service.get_settings") as settings:
            settings.return_value.TASK_RUNS_RETENTION_DAYS = 365
            removed = task_run_service.cleanup_expired(self.db, batch_size=50)
        self.assertGreaterEqual(removed, 1)
        self.assertIsNone(self.db.query(TaskRun).filter(TaskRun.task_id == "task-old").first())


if __name__ == "__main__":
    unittest.main()
