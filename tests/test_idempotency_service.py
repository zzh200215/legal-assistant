"""幂等键服务单元测试：重放 / 同 key 不同载荷 / 并发 / 失败重试 / 过期清理 / DB 唯一约束。"""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.idempotency import IdempotencyKey
from app.services.jobs.idempotency_service import IdempotencyConflictError, idempotency_service

SCOPE = "open_api.contract_review"


def _make_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class IdempotencyServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        self.db = sessionmaker(bind=self.engine)()
        self.s2 = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()
        self.s2.close()
        self.engine.dispose()

    def test_first_request_registers_in_progress(self):
        result = idempotency_service.begin(self.db, scope=SCOPE, key="k1", request_hash="h1")
        self.assertFalse(result["replay"])
        row = self.db.query(IdempotencyKey).first()
        self.assertEqual(row.status, "in_progress")
        self.assertEqual(row.scope, SCOPE)
        self.assertEqual(row.idempotency_key, "k1")

    def test_replay_returns_stored_snapshot(self):
        idempotency_service.begin(self.db, scope=SCOPE, key="k2", request_hash="h2")
        idempotency_service.complete(self.db, scope=SCOPE, key="k2",
                                     response_snapshot={"task_id": 9, "status": "queued"})
        result = idempotency_service.begin(self.db, scope=SCOPE, key="k2", request_hash="h2")
        self.assertTrue(result["replay"])
        self.assertIn('"task_id": 9', result["response_snapshot"])

    def test_same_key_different_hash_raises(self):
        idempotency_service.begin(self.db, scope=SCOPE, key="k3", request_hash="h-a")
        idempotency_service.complete(self.db, scope=SCOPE, key="k3", response_snapshot={})
        with self.assertRaises(IdempotencyConflictError):
            idempotency_service.begin(self.db, scope=SCOPE, key="k3", request_hash="h-b")

    def test_in_progress_concurrent_raises(self):
        idempotency_service.begin(self.db, scope=SCOPE, key="k4", request_hash="h4")
        with self.assertRaises(IdempotencyConflictError) as ctx:
            idempotency_service.begin(self.db, scope=SCOPE, key="k4", request_hash="h4")
        self.assertEqual(ctx.exception.code, "IDEMPOTENCY_KEY_IN_PROGRESS")

    def test_failed_allows_retry(self):
        idempotency_service.begin(self.db, scope=SCOPE, key="k5", request_hash="h5")
        idempotency_service.fail(self.db, scope=SCOPE, key="k5")
        result = idempotency_service.begin(self.db, scope=SCOPE, key="k5", request_hash="h5")
        self.assertFalse(result["replay"], "failed 状态应允许重试")

    def test_db_unique_constraint_blocks_duplicate_insert(self):
        """数据库唯一约束（scope+key）是并发最终保障：直接重复 INSERT 必须失败。"""
        idempotency_service.begin(self.db, scope=SCOPE, key="k6", request_hash="h6")
        with self.assertRaises(IntegrityError):
            self.s2.execute(text(
                "INSERT INTO idempotency_keys (scope, idempotency_key, request_hash, status, expires_at) "
                "VALUES (:s, :k, :h, 'in_progress', :e)"
            ), {"s": SCOPE, "k": "k6", "h": "h6", "e": _utcnow() + timedelta(hours=1)})
            self.s2.commit()

    def test_begin_converts_integrity_error_to_conflict(self):
        """并发同 key 同时 INSERT：begin 把 IntegrityError 转为 IdempotencyConflictError。"""
        with patch.object(self.db, "commit", side_effect=IntegrityError("stmt", {}, Exception("dup"))):
            with self.assertRaises(IdempotencyConflictError) as ctx:
                idempotency_service.begin(self.db, scope=SCOPE, key="k7", request_hash="h7")
        self.assertEqual(ctx.exception.code, "IDEMPOTENCY_KEY_CONFLICT")

    def test_expired_key_cleanup(self):
        past = _utcnow() - timedelta(hours=2)
        future = _utcnow() + timedelta(hours=2)
        for i, expires in enumerate((past, past, future)):
            self.db.add(IdempotencyKey(
                scope=SCOPE, idempotency_key=f"exp-{i}", request_hash="h",
                status="completed", expires_at=expires,
            ))
        self.db.commit()
        deleted = idempotency_service.cleanup_expired(self.db)
        self.assertEqual(deleted, 2)
        self.assertEqual(self.db.query(IdempotencyKey).count(), 1)

    def test_expired_completed_key_is_reusable(self):
        idempotency_service.begin(self.db, scope=SCOPE, key="k8", request_hash="h-old")
        idempotency_service.complete(self.db, scope=SCOPE, key="k8", response_snapshot={})
        row = self.db.query(IdempotencyKey).filter_by(idempotency_key="k8").first()
        row.expires_at = _utcnow() - timedelta(minutes=1)
        self.db.commit()
        # 过期后同 key 视为新请求，不再重放
        result = idempotency_service.begin(self.db, scope=SCOPE, key="k8", request_hash="h-new")
        self.assertFalse(result["replay"])
        self.assertEqual(self.db.query(IdempotencyKey).count(), 1)

    def test_cleanup_is_batched_and_idempotent(self):
        past = _utcnow() - timedelta(hours=1)
        for i in range(7):
            self.db.add(IdempotencyKey(
                scope=SCOPE, idempotency_key=f"b-{i}", request_hash="h",
                status="completed", expires_at=past,
            ))
        self.db.commit()
        first = idempotency_service.cleanup_expired(self.db, batch_size=3)
        second = idempotency_service.cleanup_expired(self.db, batch_size=3)
        self.assertEqual(first + second, 7)
        self.assertEqual(self.db.query(IdempotencyKey).count(), 0)


if __name__ == "__main__":
    unittest.main()
