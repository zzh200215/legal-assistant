"""RAG① 检索缓存：LRU + 可选 Redis + 内容寻址 + 批量 miss-only 计算。"""
import json
import unittest
from unittest.mock import MagicMock, patch


class RagEmbeddingCacheTests(unittest.TestCase):
    """RagEmbeddingCache 单元测试（不依赖 rag_service）"""

    def _make(self, *, capacity=4, model="m", redis_enabled=False):
        from app.services.rag_cache import RagEmbeddingCache
        return RagEmbeddingCache(
            capacity=capacity, redis_enabled=redis_enabled, ttl_seconds=60,
            redis_prefix="aibg:rag:embed", model=model,
        )

    def test_get_miss_returns_none(self):
        c = self._make()
        self.assertIsNone(c.get("hello"))

    def test_put_then_get_roundtrip(self):
        c = self._make()
        c.put("hello", [0.1, 0.2])
        self.assertEqual(c.get("hello"), [0.1, 0.2])

    def test_lru_evicts_oldest(self):
        c = self._make(capacity=2)
        c.put("a", [1]); c.put("b", [2]); c.put("c", [3])
        self.assertIsNone(c.get("a"))
        self.assertEqual(c.get("b"), [2])
        self.assertEqual(c.get("c"), [3])

    def test_model_salt_differentiates(self):
        c1 = self._make(model="m1"); c2 = self._make(model="m2")
        c1.put("hello", [1])
        self.assertIsNone(c2.get("hello"))

    def test_remove_clears(self):
        c = self._make()
        c.put("a", [1])
        c.remove("a")
        self.assertIsNone(c.get("a"))

    def test_redis_enabled_get_hit_and_put_write(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = json.dumps([0.5, 0.6]).encode("utf-8")
        with patch("app.services.rag_cache.redis.from_url", return_value=mock_redis):
            c = self._make(redis_enabled=True)
            self.assertEqual(c.get("hello"), [0.5, 0.6])  # 命中 Redis
            c.put("world", [0.7])
            mock_redis.setex.assert_called()

    def test_redis_unavailable_falls_back_to_memory(self):
        with patch("app.services.rag_cache.redis.from_url", side_effect=Exception("no redis")):
            c = self._make(redis_enabled=True)
            c.put("a", [1])
            self.assertEqual(c.get("a"), [1])  # 内存可用

    def test_get_or_compute_batch_computes_only_misses(self):
        import asyncio
        c = self._make()
        c.put("known", [0.0])

        async def compute(misses):
            return [[float(len(m))] for m in misses]

        embeddings = asyncio.run(c.get_or_compute_batch(["known", "new1", "new2"], compute))
        self.assertEqual(embeddings[0], [0.0])     # 命中缓存
        self.assertEqual(embeddings[1], [4.0])     # "new1" len 4
        self.assertEqual(embeddings[2], [4.0])     # "new2" len 4
        # 计算后也写回缓存
        self.assertEqual(c.get("new1"), [4.0])
        # 再次调用不再计算
        calls = []
        async def compute2(misses):
            calls.append(misses)
            return []
        asyncio.run(c.get_or_compute_batch(["known", "new1", "new2"], compute2))
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
