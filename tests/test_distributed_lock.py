"""分布式锁测试：双实例互斥、TTL 过期、续租、崩溃自动释放、token CAS 安全释放。

用内存 Redis stub 注入（``redis_client``），不引入 fakeredis 依赖。
"""
import unittest
import time
from unittest.mock import patch

from app.tasks.runtime import (
    acquire_task_lock,
    release_task_lock,
    renew_task_lock,
    run_locked,
    beat_lock,
)


class MemoryRedis:
    """内存 Redis 桩：支持 set(nx/ex)/get/eval(Lua CAS)/delete，带可前进的时钟。"""

    def __init__(self):
        self._base = time.monotonic()
        self._store: dict[str, tuple[str, float]] = {}

    def _now(self) -> float:
        return self._base

    def advance(self, seconds: float) -> None:
        self._base += seconds

    def set(self, key, value, nx=False, ex=None):
        now = self._now()
        existing = self._store.get(key)
        if existing and existing[1] > now:
            if nx:
                return False
            self._store[key] = (value, now + (ex or 0))
            return True
        # 已过期视为不存在
        self._store[key] = (value, now + (ex or 0))
        return True

    def get(self, key):
        item = self._store.get(key)
        if item is None:
            return None
        if item[1] <= self._now():
            self._store.pop(key, None)
            return None
        return item[0]

    def delete(self, key):
        return self._store.pop(key, None) is not None

    def eval(self, script, numkeys, *args):
        script = script.strip()
        key, argv = args[0], list(args[1:])
        if script.startswith("if redis.call('get', KEYS[1]) == ARGV[1] then"):
            if "pexpire" in script:
                # CAS_RENEW
                if self.get(key) == argv[0]:
                    item = self._store[key]
                    self._store[key] = (item[0], self._now() + int(argv[1]) / 1000.0)
                    return 1
                return 0
            # CAS_DELETE
            if self.get(key) == argv[0]:
                self.delete(key)
                return 1
            return 0
        raise AssertionError(f"unknown script: {script!r}")

    def keys(self):
        return [k for k, v in self._store.items() if v[1] > self._now()]


class DistributedLockTests(unittest.TestCase):
    def setUp(self):
        self.redis = MemoryRedis()

    def _key(self, task, scope="", window=None):
        parts = [task]
        if scope:
            parts.append(scope)
        if window:
            parts.append(window)
        return "aibg:tasklock:" + ":".join(parts)

    def test_dual_instance_mutual_exclusion(self):
        """双实例：第一个拿到锁，第二个拿不到（返回 None）。"""
        t1 = acquire_task_lock("scan_overdue_invoices", ttl_seconds=600, redis_client=self.redis)
        t2 = acquire_task_lock("scan_overdue_invoices", ttl_seconds=600, redis_client=self.redis)
        self.assertIsNotNone(t1)
        self.assertIsNone(t2, "第二实例必须拿不到锁")

    def test_scope_isolation(self):
        """同任务不同 scope（不同连接器/租户）互不阻塞。"""
        t1 = acquire_task_lock("connector_sync", scope="conn:1", ttl_seconds=600, redis_client=self.redis)
        t2 = acquire_task_lock("connector_sync", scope="conn:2", ttl_seconds=600, redis_client=self.redis)
        self.assertIsNotNone(t1)
        self.assertIsNotNone(t2)

    def test_ttl_expiry_allows_reacquire(self):
        """TTL 过期后锁自动释放，可重新获取（崩溃场景）。"""
        t1 = acquire_task_lock("connector_sync", scope="conn:9", ttl_seconds=5, redis_client=self.redis)
        self.assertIsNotNone(t1)
        self.redis.advance(6)
        t2 = acquire_task_lock("connector_sync", scope="conn:9", ttl_seconds=5, redis_client=self.redis)
        self.assertIsNotNone(t2, "锁过期后应可重获")

    def test_renew_extends_ttl(self):
        """续租延长 TTL：未续租会过期，续租后不过期。"""
        t1 = acquire_task_lock("connector_sync", scope="conn:3", ttl_seconds=10, redis_client=self.redis)
        self.redis.advance(8)
        ok = renew_task_lock("connector_sync", scope="conn:3", token=t1, ttl_seconds=10, redis_client=self.redis)
        self.assertTrue(ok)
        self.redis.advance(8)  # 原 TTL 10s 已过，但续租后从 8s 重新计 10s
        self.assertIsNotNone(self.redis.get(self._key("connector_sync", scope="conn:3")),
                             "续租后锁应仍在")

    def test_renew_wrong_token_noop(self):
        """错 token 续租不生效（锁已被他 worker 重获时不能续到别人的锁）。"""
        t1 = acquire_task_lock("t", ttl_seconds=10, redis_client=self.redis)
        self.redis.delete(self._key("t"))
        t2 = acquire_task_lock("t", ttl_seconds=10, redis_client=self.redis)
        self.assertIsNotNone(t2)
        self.assertFalse(renew_task_lock("t", token=t1, ttl_seconds=10, redis_client=self.redis),
                         "旧 token 续租必须失败")

    def test_release_wrong_token_noop(self):
        """CAS 释放：错 token 释放是 noop，不误删他 worker 的锁。"""
        t1 = acquire_task_lock("t", ttl_seconds=10, redis_client=self.redis)
        self.redis.delete(self._key("t"))
        t2 = acquire_task_lock("t", ttl_seconds=10, redis_client=self.redis)
        self.assertIsNotNone(t2, "模拟他 worker 已重获锁")
        release_task_lock("t", token=t1, redis_client=self.redis)  # 旧 token 释放
        self.assertIsNotNone(self.redis.get(self._key("t")), "错 token 释放不得删锁")

    def test_release_correct_token_frees_lock(self):
        t1 = acquire_task_lock("t", ttl_seconds=10, redis_client=self.redis)
        release_task_lock("t", token=t1, redis_client=self.redis)
        self.assertIsNone(self.redis.get(self._key("t")))

    def test_run_locked_executes_and_releases(self):
        """run_locked：获得锁执行 fn，finally 释放；未获锁走 on_skip。"""
        calls = []
        with patch("app.tasks.runtime.redis.from_url", return_value=self.redis):
            result = run_locked(
                "t", ttl_seconds=10, fn=lambda: calls.append("run") or "ok",
                on_skip=lambda: calls.append("skip"),
            )
        self.assertEqual(result, "ok")
        self.assertEqual(calls, ["run"])
        self.assertIsNone(self.redis.get(self._key("t")), "执行后锁必须释放")

    def test_run_locked_skip_when_locked(self):
        acquire_task_lock("t", ttl_seconds=10, redis_client=self.redis)
        skipped = []
        with patch("app.tasks.runtime.redis.from_url", return_value=self.redis):
            result = run_locked(
                "t", ttl_seconds=10, fn=lambda: "run", on_skip=lambda: skipped.append(1),
            )
        self.assertIsNone(result)
        self.assertEqual(skipped, [1], "未获锁应调用 on_skip")

    def test_beat_lock_decorator_skips_when_locked(self):
        with patch("app.tasks.runtime.redis.from_url", return_value=self.redis):

            @beat_lock("my_beat_task", ttl_seconds=600)
            def _task():
                return "executed"

            result = _task()
            self.assertEqual(result, "executed")

            # 手动占锁后再次执行 → 安全跳过返回 None
            acquire_task_lock("my_beat_task", ttl_seconds=600, redis_client=self.redis)
            result2 = _task()
            self.assertIsNone(result2)


if __name__ == "__main__":
    unittest.main()
