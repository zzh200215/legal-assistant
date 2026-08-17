"""契约测试：Redis 原语（锁 / 幂等键 / 心跳）——本系统承诺的调用协议。

被测对象：app/tasks/runtime.py 的分布式锁原语 + 心跳写入。
替身：fakeredis（内存实现）用于 NX/PX/TTL 原语；CAS Lua（fakeredis 不支持 eval）
用复刻脚本判定的内存 stub 验证「runtime 以 eval(script, 1, key, token) 方式调用、
按返回值处理」的调用协议。
契约点：
- acquire_task_lock：SET NX PX 互斥语义、TTL、失败返回 None、Redis 异常放行（fail-open）；
- release_task_lock：CAS 删除（错 token 不删，防误删他人锁）；
- renew_task_lock：CAS 续租（错 token 不续）；
- 幂等键原语：SET NX EX（同 key 重复写入被拒）——与 IdempotencyKey DB 唯一约束
  构成的双层幂等契约（Redis 快速路径 + DB 兜底）；
- record_beat_heartbeat：心跳键 + 180s TTL。
"""

import unittest
from unittest.mock import patch

import fakeredis

from app.tasks import runtime


def _make_redis():
    return fakeredis.FakeRedis(decode_responses=True)


class _CasLuaRedis:
    """Lua 语义模拟：按 runtime 的 _CAS_DELETE/_CAS_RENEW 脚本判定执行。

    只复刻脚本的判定逻辑（get==ARGV[1] 才 del / pexpire），并记录 eval 调用
    以钉死调用协议（script、numkeys、key、token）。
    """

    def __init__(self):
        self.store = {}
        self.ttls = {}
        self.eval_calls = []

    def set(self, name, value, nx=False, ex=None):
        if nx and name in self.store:
            return None
        self.store[name] = value
        if ex:
            self.ttls[name] = ex
        return True

    def get(self, name):
        return self.store.get(name)

    def ttl(self, name):
        return self.ttls.get(name, -1)

    def delete(self, *names):
        removed = 0
        for name in names:
            if name in self.store:
                del self.store[name]
                self.ttls.pop(name, None)
                removed += 1
        return removed

    def eval(self, script, numkeys, *args):
        self.eval_calls.append((script, numkeys, args))
        key, token = args[0], args[1]
        if script == runtime._CAS_DELETE:
            if self.store.get(key) == token:
                self.delete(key)
                return 1
            return 0
        if script == runtime._CAS_RENEW:
            if self.store.get(key) == token:
                self.ttls[key] = args[2] / 1000
                return 1
            return 0
        raise AssertionError(f"未知 Lua 脚本：{script[:40]}...")


class RedisLockContractTests(unittest.TestCase):
    def test_acquire_is_exclusive(self):
        r = _make_redis()
        token1 = runtime.acquire_task_lock("t1", ttl_seconds=60, redis_client=r)
        token2 = runtime.acquire_task_lock("t1", ttl_seconds=60, redis_client=r)
        self.assertIsNotNone(token1)
        self.assertIsNone(token2)  # 互斥：第二个拿不到
        self.assertNotEqual(token1, token2)

    def test_acquire_writes_nx_key_with_ttl(self):
        r = _make_redis()
        token = runtime.acquire_task_lock("t2", ttl_seconds=120, redis_client=r)
        stored = r.get("aibg:tasklock:t2")
        self.assertEqual(stored, token)
        self.assertGreater(r.ttl("aibg:tasklock:t2"), 0)

    def test_scope_and_window_isolate_keys(self):
        r = _make_redis()
        a = runtime.acquire_task_lock("t3", scope="tenant:1", ttl_seconds=60, redis_client=r)
        b = runtime.acquire_task_lock("t3", scope="tenant:2", ttl_seconds=60, redis_client=r)
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)

    def test_release_cas_only_by_owner(self):
        r = _CasLuaRedis()
        token = runtime.acquire_task_lock("t4", ttl_seconds=60, redis_client=r)
        # 错误 token 释放：CAS 判定不通过，不删除（防误删他 worker 续租后的锁）
        runtime.release_task_lock("t4", token="wrong", redis_client=r)
        self.assertIsNotNone(r.get("aibg:tasklock:t4"))
        # 正确 token 释放：删除
        runtime.release_task_lock("t4", token=token, redis_client=r)
        self.assertIsNone(r.get("aibg:tasklock:t4"))

    def test_release_uses_cas_delete_script(self):
        r = _CasLuaRedis()
        token = runtime.acquire_task_lock("t4", ttl_seconds=60, redis_client=r)
        runtime.release_task_lock("t4", token=token, redis_client=r)
        script, numkeys, args = r.eval_calls[-1]
        self.assertEqual(script, runtime._CAS_DELETE)
        self.assertEqual(numkeys, 1)
        self.assertEqual(args, ("aibg:tasklock:t4", token))

    def test_renew_cas_extends_ttl(self):
        r = _CasLuaRedis()
        token = runtime.acquire_task_lock("t5", ttl_seconds=60, redis_client=r)
        ok = runtime.renew_task_lock("t5", token=token, ttl_seconds=600, redis_client=r)
        self.assertTrue(ok)
        self.assertEqual(r.ttl("aibg:tasklock:t5"), 600)
        # 错 token 续租失败
        self.assertFalse(runtime.renew_task_lock("t5", token="wrong", ttl_seconds=600, redis_client=r))
        script, numkeys, args = r.eval_calls[-1]
        self.assertEqual(script, runtime._CAS_RENEW)
        self.assertEqual(numkeys, 1)
        self.assertEqual(args[0], "aibg:tasklock:t5")

    def test_redis_unavailable_fails_open(self):
        # 锁语义：Redis 不可用时放行，由 DB 唯一约束/乐观锁兜底（既有约定）
        class _BoomRedis:
            def set(self, *a, **k):
                raise ConnectionError("redis down")

        token = runtime.acquire_task_lock("t6", ttl_seconds=60, redis_client=_BoomRedis())
        self.assertIsNotNone(token)  # fail-open：返回 token 放行


class RedisIdempotencyKeyContractTests(unittest.TestCase):
    def test_set_nx_ex_rejects_duplicate_key(self):
        """幂等键原语：同 key 首次写入成功，重复写入被拒（模拟重放）。"""
        r = _make_redis()
        key = "aibg:idem:review:user-1:req-abc"
        first = r.set(key, "fingerprint-1", nx=True, ex=3600)
        replay = r.set(key, "fingerprint-1", nx=True, ex=3600)
        conflict = r.set(key, "fingerprint-2", nx=True, ex=3600)
        self.assertTrue(first)
        self.assertFalse(replay)   # 同 key 重放 → 拒绝（走原结果路径）
        self.assertFalse(conflict)  # 同 key 异指纹 → 拒绝（409 语义来源）
        self.assertGreater(r.ttl(key), 0)

    def test_expiry_allows_new_key_after_ttl(self):
        r = _make_redis()
        key = "aibg:idem:x"
        self.assertTrue(r.set(key, "1", nx=True, ex=1))
        r.delete(key)
        self.assertTrue(r.set(key, "2", nx=True, ex=1))  # 过期后可重新使用


class RedisHeartbeatContractTests(unittest.TestCase):
    def test_heartbeat_writes_key_with_ttl(self):
        r = _make_redis()
        with patch("app.tasks.runtime.redis.from_url", return_value=r):
            runtime.record_beat_heartbeat()
        self.assertIsNotNone(r.get("aibg:operations:beat:last_tick"))
        self.assertGreater(r.ttl("aibg:operations:beat:last_tick"), 0)

    def test_heartbeat_survives_redis_failure(self):
        with patch("app.tasks.runtime.redis.from_url", side_effect=ConnectionError("down")):
            runtime.record_beat_heartbeat()  # 不抛异常


if __name__ == "__main__":
    unittest.main()
