"""原子配额测试：并发不超额、重复 usage event 不重复扣减、reserve/commit/release。"""
import os
import tempfile
import unittest
from threading import Thread

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.core.auth import hash_password
from app.core.database import Base
from app.models.subscription import QuotaUsage
from app.models.usage_reservation import UsageReservation
from app.models.user import User, UserStatus
from app.services.billing.subscription_service import QuotaExceededError, subscription_service


class AtomicQuotaTests(unittest.TestCase):
    def setUp(self):
        # 临时文件 SQLite：多连接 + busy timeout，支撑真实并发条件 UPDATE
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.engine = create_engine(
            f"sqlite+pysqlite:///{self._tmp.name}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.SessionLocal()
        self.user = User(username="q1", email="q1@x.com", hashed_password=hash_password("pw"),
                         role="user", status=UserStatus.active.value)
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)
        subscription_service.ensure_default_plans(self.db)

    def tearDown(self):
        try:
            self.db.close()
        finally:
            self.engine.dispose()
        self._tmp.close()
        try:
            os.unlink(self._tmp.name)
        except OSError:
            pass

    def test_no_over_quota_with_concurrent_reserves(self):
        """并发预留不超额：免费版咨询上限 5，20 个并发请求最多成功 5 个。"""
        results: list[dict] = []
        errors: list[str] = []

        def worker(i: int):
            session = self.SessionLocal()
            try:
                try:
                    subscription_service.reserve_quota(
                        db=session, user_id=self.user.id, quota_type="consultation",
                        usage_event_id=f"c-{i}")
                    results.append({"ok": True, "i": i})
                except QuotaExceededError:
                    results.append({"ok": False, "i": i})
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))
            finally:
                session.close()

        threads = [Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        ok_count = sum(1 for r in results if r["ok"])
        self.assertEqual(ok_count, 5, f"应恰好 5 个成功，实际 {ok_count}; errors={errors}")
        usage = self.db.query(QuotaUsage).filter(QuotaUsage.user_id == self.user.id).first()
        self.assertEqual(usage.consultation_count, 5)

    def test_duplicate_usage_event_does_not_double_consume(self):
        r1 = subscription_service.reserve_quota(
            db=self.db, user_id=self.user.id, quota_type="consultation",
            usage_event_id="evt-dup")
        r2 = subscription_service.reserve_quota(
            db=self.db, user_id=self.user.id, quota_type="consultation",
            usage_event_id="evt-dup")
        self.assertEqual(r1.id, r2.id, "同 usage_event_id 返回同一条预留")
        self.assertEqual(self.db.query(QuotaUsage).first().consultation_count, 1)
        self.assertEqual(self.db.query(UsageReservation).count(), 1)

    def test_reserve_commit_release(self):
        r = subscription_service.reserve_quota(
            db=self.db, user_id=self.user.id, quota_type="consultation",
            usage_event_id="evt-release")
        self.assertEqual(r.status, "reserved")
        self.assertEqual(self.db.query(QuotaUsage).first().consultation_count, 1)
        # 失败 → release 回滚
        subscription_service.release_usage(db=self.db, usage_event_id="evt-release")
        self.assertEqual(self.db.query(QuotaUsage).first().consultation_count, 0)
        self.db.refresh(r)
        self.assertEqual(r.status, "released")
        # 重新预留 → commit
        r2 = subscription_service.reserve_quota(
            db=self.db, user_id=self.user.id, quota_type="consultation",
            usage_event_id="evt-commit")
        subscription_service.commit_usage(db=self.db, usage_event_id="evt-commit")
        self.assertEqual(self.db.query(QuotaUsage).first().consultation_count, 1)
        self.db.refresh(r2)
        self.assertEqual(r2.status, "committed")
        # commit 不可重复（幂等）
        subscription_service.commit_usage(db=self.db, usage_event_id="evt-commit")
        self.assertEqual(self.db.query(QuotaUsage).first().consultation_count, 1)

    def test_try_consume_exhausted(self):
        # 免费版咨询配额 5；耗尽后返回稳定错误码
        for i in range(5):
            self.assertTrue(subscription_service.try_consume_quota(
                db=self.db, user_id=self.user.id, quota_type="consultation",
                usage_event_id=f"c-{i}")["ok"])
        result = subscription_service.try_consume_quota(
            db=self.db, user_id=self.user.id, quota_type="consultation", usage_event_id="c-6")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "QUOTA_EXCEEDED")


if __name__ == "__main__":
    unittest.main()
