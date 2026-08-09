"""乐观锁（version_id_col）单元测试：版本自动递增、并发冲突、409 映射。"""
import unittest

from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.exc import StaleDataError
from starlette.testclient import TestClient

from app.core.api_response import (
    ApiResponseMiddleware,
    stale_data_exception_handler,
    unhandled_exception_handler,
)
from app.core.database import Base
from app.models.document import Document
from app.models.legal import ContractReview, LegalCase, LegalDraft
from app.models.legal_contract import LegalContract
from app.models.org import Organization
from app.models.task import Task


def _make_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _org(db):
    org = Organization(name="LockOrg", code="LCK")
    db.add(org)
    db.commit()
    return org


class OptimisticLockModelTests(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        self.factory = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.factory()
        _org(self.db)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _task(self, db):
        t = Task(user_id=1, title="并发任务")
        db.add(t)
        db.commit()
        return t

    def test_insert_sets_version_one(self):
        t = self._task(self.db)
        self.assertEqual(t.version, 1)
        d = Document(user_id=1, title="d", file_path="p", file_type="pdf")
        self.db.add(d)
        self.db.commit()
        self.assertEqual(d.version, 1)

    def test_update_auto_increments_version(self):
        t = self._task(self.db)
        t.status = "done"
        self.db.commit()
        self.assertEqual(t.version, 2)
        fresh = self.factory().query(Task).get(t.id)
        self.assertEqual(fresh.version, 2)

    def test_sequential_sessions_do_not_conflict(self):
        t = self._task(self.db)
        s2 = self.factory()
        t2 = s2.query(Task).get(t.id)
        t2.status = "in_progress"
        s2.commit()
        s3 = self.factory()
        t3 = s3.query(Task).get(t.id)
        t3.status = "done"
        s3.commit()
        self.assertEqual(self.factory().query(Task).get(t.id).version, 3)
        s2.close()
        s3.close()

    def test_concurrent_update_raises_stale_data_error(self):
        """两个 Session 同时更新同一条记录：后提交者抛 StaleDataError。"""
        t = self._task(self.db)
        s2 = self.factory()
        s3 = self.factory()
        t2 = s2.query(Task).get(t.id)
        t3 = s3.query(Task).get(t.id)
        t3.status = "done"
        s3.commit()  # v1 -> v2
        t2.status = "in_progress"
        with self.assertRaises(StaleDataError):
            s2.commit()  # 基于旧版本 v1，冲突
        s2.close()
        s3.close()

    def test_legacy_backfilled_row_updates_ok(self):
        """迁移回填（version=1 的存量行）更新正常。"""
        t = self._task(self.db)
        from sqlalchemy import text

        self.db.execute(text("UPDATE tasks SET version = 1 WHERE id = :i"), {"i": t.id})
        self.db.commit()
        s2 = self.factory()
        t2 = s2.query(Task).get(t.id)
        t2.status = "done"
        s2.commit()  # 不抛错
        self.assertEqual(s2.query(Task).get(t.id).version, 2)
        s2.close()

    def test_all_locked_models_have_version_id_col(self):
        """7 个需并发保护的模型均已配置 version_id_col。"""
        for cls in (Document, Task, LegalCase, LegalContract, Organization, ContractReview, LegalDraft):
            mapper = cls.__mapper__
            self.assertIsNotNone(mapper.version_id_col, f"{cls.__name__} 缺少 version_id_col")
        # 审查结果/草稿用 row_version 列；其余用 version 列
        self.assertEqual(ContractReview.__mapper__.version_id_col.name, "row_version")
        self.assertEqual(LegalDraft.__mapper__.version_id_col.name, "row_version")
        self.assertEqual(Document.__mapper__.version_id_col.name, "version")


class StaleDataHandlerTests(unittest.TestCase):
    """乐观锁冲突在 API 层映射为 409 Conflict。"""

    def test_stale_data_error_maps_to_409(self):
        app = FastAPI()
        app.add_exception_handler(Exception, unhandled_exception_handler)
        app.add_exception_handler(StaleDataError, stale_data_exception_handler)
        app.add_middleware(ApiResponseMiddleware)

        @app.get("/conflict")
        def conflict():
            raise StaleDataError("concurrent update")

        client = TestClient(app)
        resp = client.get("/conflict")
        self.assertEqual(resp.status_code, 409)
        body = resp.json()
        self.assertEqual(body["success"], False)
        self.assertEqual(body["error"]["code"], "CONCURRENT_UPDATE_CONFLICT")

    def test_other_errors_still_500(self):
        app = FastAPI()
        app.add_exception_handler(Exception, unhandled_exception_handler)
        app.add_exception_handler(StaleDataError, stale_data_exception_handler)

        @app.get("/boom")
        def boom():
            raise RuntimeError("boom")

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/boom")
        self.assertEqual(resp.status_code, 500)


if __name__ == "__main__":
    unittest.main()
