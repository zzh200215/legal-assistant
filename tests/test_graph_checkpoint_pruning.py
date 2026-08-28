"""checkpoint 保留窗口清理：一次问答 / 一次 Run 一个 thread，不回收则 sqlite 只增不减。

按时间窗口清、而不是「Run 一结束就删」：已完成 Run 的 checkpoint 仍要能回放
（tests/test_agent_graph_durability.py 就直接读完成 Run 的 checkpoint），窗口之外的 thread
才是真的无人可用。还能 resume 的 Run 走 thread_id 白名单保护——它们可能长期停在人工审批的
中断点上，时间戳早于窗口也不能删。
"""

import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.agent import AgentRun
from app.models.user import User
from app.services.agent.agent_service import AgentService, agent_run_thread_id
from app.tasks import ops_tasks
from app.workflows.langgraph_compat import (
    SQLITE_SAVER_AVAILABLE,
    build_checkpointer,
    close_checkpointer,
    prune_checkpoint_threads,
)


def _iso(days_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()


class _Tuple:
    """``CheckpointTuple`` 的最小替身：清理只看 thread_id 与 ``checkpoint["ts"]``。"""

    def __init__(self, thread_id: str, ts):
        self.config = {"configurable": {"thread_id": thread_id}}
        self.checkpoint = {"ts": ts}


class _FakeSaver:
    """只实现清理用到的两个公开方法，用来构造真 saver 里不好造的边界。"""

    def __init__(self, *tuples: _Tuple, failing: tuple[str, ...] = ()):
        self._tuples = list(tuples)
        self._failing = set(failing)
        self.deleted: list[str] = []

    def list(self, config, *, filter=None, before=None, limit=None):  # noqa: A002 - 对齐 saver 签名
        return iter(self._tuples)

    def delete_thread(self, thread_id: str) -> None:
        if thread_id in self._failing:
            raise RuntimeError("database is locked")
        self.deleted.append(thread_id)


class _ListOnlySaver:
    """没有 ``delete_thread`` 的 saver：清理只能退化为 no-op，不能报错。"""

    def __init__(self, *tuples: _Tuple):
        self._tuples = list(tuples)

    def list(self, config, *, filter=None, before=None, limit=None):  # noqa: A002 - 对齐 saver 签名
        return iter(self._tuples)


class PruneWindowTests(unittest.TestCase):
    def test_only_threads_outside_the_window_are_deleted(self):
        saver = _FakeSaver(_Tuple("rag-7-old", _iso(30)), _Tuple("rag-7-new", _iso(1)))

        report = prune_checkpoint_threads(saver, older_than_days=7)

        self.assertEqual(saver.deleted, ["rag-7-old"])
        self.assertTrue(report["available"])
        self.assertEqual((report["threads"], report["expired"], report["deleted"]), (2, 1, 1))

    def test_the_newest_checkpoint_decides_the_thread(self):
        """一个 thread 有多份 checkpoint：只要最新那份在窗口内，整个 thread 都留着。"""
        saver = _FakeSaver(_Tuple("agent-run-1-t", _iso(30)), _Tuple("agent-run-1-t", _iso(1)))

        prune_checkpoint_threads(saver, older_than_days=7)

        self.assertEqual(saver.deleted, [])

    def test_resumable_runs_are_protected_even_when_stale(self):
        """停在审批断点上的 Run 可能挂很久：删掉它等于让这次 Run 再也无法恢复。"""
        saver = _FakeSaver(_Tuple("agent-run-9-trace", _iso(30)), _Tuple("rag-7-old", _iso(30)))

        report = prune_checkpoint_threads(saver, older_than_days=7, keep_thread_ids=["agent-run-9-trace"])

        self.assertEqual(saver.deleted, ["rag-7-old"])
        self.assertEqual((report["expired"], report["protected"], report["deleted"]), (2, 1, 1))

    def test_dry_run_counts_candidates_without_deleting(self):
        saver = _FakeSaver(_Tuple("rag-7-old", _iso(30)))

        report = prune_checkpoint_threads(saver, older_than_days=7, dry_run=True)

        self.assertEqual(saver.deleted, [])
        self.assertEqual((report["candidates"], report["deleted"]), (1, 0))
        self.assertTrue(report["dry_run"])

    def test_unreadable_timestamp_keeps_the_thread(self):
        """读不懂时间戳就当它是新的：宁可留着，也不凭猜删掉可能还要 resume 的状态。"""
        saver = _FakeSaver(
            _Tuple("rag-broken", "not-a-timestamp"),
            _Tuple("rag-missing", None),
            _Tuple("rag-missing", _iso(30)),
        )

        report = prune_checkpoint_threads(saver, older_than_days=7)

        self.assertEqual(saver.deleted, [])
        self.assertEqual((report["threads"], report["expired"]), (2, 0))

    def test_one_failed_delete_does_not_abort_the_sweep(self):
        saver = _FakeSaver(_Tuple("rag-a", _iso(30)), _Tuple("rag-b", _iso(30)), failing=("rag-a",))

        report = prune_checkpoint_threads(saver, older_than_days=7)

        self.assertEqual(saver.deleted, ["rag-b"])
        self.assertEqual((report["deleted"], report["failed"]), (1, 1))

    def test_saver_without_delete_support_is_a_noop(self):
        report = prune_checkpoint_threads(_ListOnlySaver(_Tuple("rag-old", _iso(30))), older_than_days=7)

        self.assertFalse(report["available"])
        self.assertEqual(report["deleted"], 0)

    def test_missing_checkpointer_is_a_noop(self):
        """回退引擎没有 checkpointer：清理任务不该因此失败。"""
        self.assertFalse(prune_checkpoint_threads(None, older_than_days=7)["available"])


class RealSqliteSaverPruneTests(unittest.TestCase):
    """对着真 SqliteSaver 跑一遍：thread 枚举、时间戳、delete_thread 全是它的真实实现。"""

    def setUp(self):
        if not SQLITE_SAVER_AVAILABLE:
            self.skipTest("langgraph-checkpoint-sqlite 未安装")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # 独立临时库：不碰 conftest 里那份共享测试 checkpoint 文件。
        env = patch.dict(os.environ, {"LANGGRAPH_CHECKPOINT_DB": os.path.join(tmp.name, "ck.sqlite")})
        env.start()
        self.addCleanup(env.stop)
        self.saver = build_checkpointer()
        self.addCleanup(close_checkpointer, self.saver)

    @staticmethod
    def _config(thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

    def _write(self, thread_id: str, days_ago: float) -> None:
        from langgraph.checkpoint.base import empty_checkpoint

        checkpoint = empty_checkpoint()
        checkpoint["ts"] = _iso(days_ago)
        written = self.saver.put(self._config(thread_id), checkpoint, {"source": "input", "step": 0, "parents": {}}, {})
        self.saver.put_writes(written, [("messages", "x")], "task-1")

    def test_stale_thread_is_dropped_and_the_fresh_one_stays_resumable(self):
        self._write("rag-7-old", 30)
        self._write("agent-run-1-trace", 1)

        report = prune_checkpoint_threads(self.saver, older_than_days=7)

        self.assertEqual((report["threads"], report["deleted"], report["failed"]), (2, 1, 0))
        self.assertIsNone(self.saver.get_tuple(self._config("rag-7-old")))
        self.assertIsNotNone(self.saver.get_tuple(self._config("agent-run-1-trace")))


class ThreadIdContractTests(unittest.TestCase):
    """白名单与图 config 必须用同一个公式算 thread_id——漂了就会误删审批中的 Run。"""

    class _Run:
        def __init__(self, run_id: int, trace_id: str | None):
            self.id = run_id
            self.trace_id = trace_id

    def test_helper_matches_the_graph_config(self):
        run = self._Run(41, "trace-abc")

        self.assertEqual(
            AgentService._graph_config(run)["configurable"]["thread_id"],
            agent_run_thread_id(run),
        )

    def test_trace_id_participates_but_is_optional(self):
        self.assertEqual(agent_run_thread_id(self._Run(41, "trace-abc")), "agent-run-41-trace-abc")
        self.assertEqual(agent_run_thread_id(self._Run(41, None)), "agent-run-41")


class PruneTaskTests(unittest.TestCase):
    """定期任务的接线：开关、活跃 Run 白名单、用完关掉自己开的连接。"""

    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        self.addCleanup(engine.dispose)
        self.Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        db = self.Session()
        user = User(username="prune", email="prune@example.com", hashed_password="secret")
        db.add(user)
        db.commit()
        runs = [
            AgentRun(user_id=user.id, goal="等审批", status="awaiting_approval", trace_id="t-paused"),
            AgentRun(user_id=user.id, goal="已完成", status="completed", trace_id="t-done"),
        ]
        db.add_all(runs)
        db.commit()
        self.paused_thread, self.done_thread = (agent_run_thread_id(run) for run in runs)
        db.close()

    def _run_task(self, saver, *, enabled=True, retention_days=7):
        with (
            patch.object(ops_tasks, "SessionLocal", self.Session),
            patch.object(ops_tasks, "get_settings") as settings,
            # Redis 不可用时 beat_lock 放行（既有降级语义），测试不依赖 Redis 也不发网络请求。
            patch("app.tasks.runtime.redis.from_url", side_effect=OSError("no redis in tests")),
            patch("app.workflows.langgraph_compat.build_checkpointer", return_value=saver),
        ):
            settings.return_value.GRAPH_CHECKPOINT_PRUNE_ENABLED = enabled
            settings.return_value.GRAPH_CHECKPOINT_RETENTION_DAYS = retention_days
            return ops_tasks.prune_graph_checkpoints_task()

    def test_active_runs_are_whitelisted_and_stale_qa_threads_are_dropped(self):
        saver = _FakeSaver(
            _Tuple(self.paused_thread, _iso(30)),
            _Tuple(self.done_thread, _iso(30)),
            _Tuple("rag-7-abcdef", _iso(30)),
        )

        result = self._run_task(saver)

        self.assertEqual(sorted(saver.deleted), sorted([self.done_thread, "rag-7-abcdef"]))
        self.assertEqual((result["enabled"], result["deleted"], result["protected"]), (True, 2, 1))

    def test_disabled_switch_skips_the_sweep(self):
        saver = _FakeSaver(_Tuple("rag-7-abcdef", _iso(30)))

        self.assertEqual(self._run_task(saver, enabled=False), {"enabled": False})
        self.assertEqual(saver.deleted, [])

    def test_the_task_closes_the_connection_it_opened(self):
        """每次 beat 都新开一条 sqlite 连接，不关就是每天泄漏一个 fd。"""
        closed = []
        saver = _FakeSaver(_Tuple("rag-7-abcdef", _iso(30)))
        saver.conn = type("_Conn", (), {"close": lambda _self: closed.append(True)})()

        self._run_task(saver)

        self.assertEqual(closed, [True])


if __name__ == "__main__":
    unittest.main()
