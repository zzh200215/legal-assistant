"""统一事务上下文（session_scope）与 get_db 事务边界单元测试。

session_scope / get_db 都使用 module 级 SessionLocal；测试通过 patch 指向
独立的内存 SQLite sessionmaker，不触达真实数据库引擎。
"""
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core import database as database_module
from app.core.database import Base
from app.models.operation_log import OperationLog


def _make_session_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


class SessionScopeTests(unittest.TestCase):
    def setUp(self):
        self.factory = _make_session_factory()

    def test_session_scope_commits_on_success(self):
        with patch.object(database_module, "SessionLocal", self.factory):
            with database_module.session_scope() as db:
                db.add(OperationLog(module="m", action="a", user_id=1))
        fresh = self.factory()
        self.assertEqual(fresh.query(OperationLog).count(), 1)
        fresh.close()

    def test_transaction_committed_exactly_once(self):
        """业务操作在事务边界只提交一次：session_scope 退出时恰好一次 commit。"""
        from app.core.db_monitor import DBMonitor, install_db_monitor

        engine = self.factory.kw["bind"]
        monitor = DBMonitor()
        install_db_monitor(engine, monitor)
        with patch.object(database_module, "SessionLocal", self.factory):
            with database_module.session_scope() as db:
                db.add(OperationLog(module="m", action="a", user_id=1))
                db.flush()
        self.assertEqual(monitor.snapshot()["commit_count"], 1)

    def test_session_scope_rolls_back_on_error(self):
        with patch.object(database_module, "SessionLocal", self.factory):
            with self.assertRaises(RuntimeError):
                with database_module.session_scope() as db:
                    db.add(OperationLog(module="m", action="a", user_id=1))
                    raise RuntimeError("boom")
        fresh = self.factory()
        self.assertEqual(fresh.query(OperationLog).count(), 0, "异常时应回滚")
        fresh.close()

    def test_get_db_rolls_back_on_exception(self):
        with patch.object(database_module, "SessionLocal", self.factory):
            gen = database_module.get_db()
            session = next(gen)
            session.add(OperationLog(module="m", action="a", user_id=1))
            with self.assertRaises(RuntimeError):
                gen.throw(RuntimeError("boom"))
        fresh = self.factory()
        self.assertEqual(fresh.query(OperationLog).count(), 0, "get_db 异常时应回滚")
        fresh.close()

    def test_get_db_committed_work_survives(self):
        """service 已 commit 的事务在端点异常时不受 rollback 影响。"""
        with patch.object(database_module, "SessionLocal", self.factory):
            gen = database_module.get_db()
            session = next(gen)
            session.add(OperationLog(module="m", action="a", user_id=1))
            session.commit()
            with self.assertRaises(RuntimeError):
                gen.throw(RuntimeError("boom"))
        fresh = self.factory()
        self.assertEqual(fresh.query(OperationLog).count(), 1, "已提交的事务不应被回滚")
        fresh.close()


if __name__ == "__main__":
    unittest.main()
