"""RAG③ BM25 关键词召回：替代全表扫描 + 按 where 剪枝 + 小语料回退 jieba 扫描。"""
import asyncio
import sys
import unittest
from unittest.mock import patch

from app.services.rag.rag_service import RAGService


class BM25RecallTests(unittest.TestCase):
    """_keyword_multi_recall 的 BM25 路径"""

    def setUp(self):
        self.service = RAGService()
        self.service._bm25_stale = True
        self.service._bm25_index = None
        self.service._bm25_items = []

    def _rows4(self):
        # 4 文档：货款/支付 仅出现在 c1（df=1 < N/2）→ Okapi idf 为正，BM25 有分
        return {
            "ids": ["c1", "c2", "c3", "c4"],
            "documents": [
                "甲方应支付货款一百万元",
                "劳动合同期限为三年",
                "会议纪要：项目启动会安排",
                "差旅报销标准说明",
            ],
            "metadatas": [
                {"document_id": 1, "user_id": 7},
                {"document_id": 2, "user_id": 8},
                {"document_id": 3, "user_id": 8},
                {"document_id": 4, "user_id": 8},
            ],
        }

    def _rows2(self):
        # 2 文档：任何词 df=1=N/2 → Okapi idf=0 → BM25 空 → 回退 jieba
        return {
            "ids": ["c1", "c2"],
            "documents": ["甲方应支付货款一百万元", "劳动合同期限为三年"],
            "metadatas": [
                {"document_id": 1, "user_id": 7},
                {"document_id": 2, "user_id": 8},
            ],
        }

    def _run(self, rows, variants, where, candidate_limit=5):
        with patch.object(self.service.collection, "get", return_value=rows):
            return asyncio.run(self.service._keyword_multi_recall(
                variants, where=where, candidate_limit=candidate_limit,
            ))

    def test_bm25_returns_matches_pruned_by_where(self):
        """BM25 命中 + 按 where 剪枝（user 隔离不泄漏）。"""
        res = self._run(self._rows4(), ["货款支付"], where={"user_id": 7})
        self.assertTrue(res)
        ids = [c["id"] for c in res]
        self.assertIn("c1", ids)
        self.assertNotIn("c2", ids)
        self.assertNotIn("c3", ids)
        self.assertEqual(res[0]["routes"], {"keyword"})
        self.assertTrue(0.0 <= res[0]["keyword_score"] <= 1.0)

    def test_small_corpus_bm25_empty_falls_back_to_scan(self):
        """小语料（2 文档）BM25 idf 退化为 0 → 空 → 回退 jieba 扫描保证召回。"""
        res = self._run(self._rows2(), ["货款支付"], where={"user_id": 7})
        self.assertTrue(res)
        self.assertIn("c1", [c["id"] for c in res])
        self.assertNotIn("c2", [c["id"] for c in res])

    def test_bm25_disabled_falls_back_to_jieba_scan(self):
        with patch("app.services.rag.rag_service.settings.RAG_BM25_ENABLED", False):
            res = self._run(self._rows4(), ["货款支付"], where=None)
        self.assertTrue(res)
        self.assertIn("c1", [c["id"] for c in res])

    def test_bm25_import_failure_falls_back_to_jieba_scan(self):
        with patch.dict(sys.modules, {"rank_bm25": None}):
            res = self._run(self._rows4(), ["货款支付"], where=None)
        self.assertTrue(res)
        self.assertIn("c1", [c["id"] for c in res])

    def test_stats_count_build_and_ready_index(self):
        """索引构建成功后：builds +1、index_size 记条目数、last_build_ms 有值、不再 stale。"""
        self._run(self._rows4(), ["货款支付"], where=None)
        stats = self.service.bm25_stats()
        self.assertGreaterEqual(stats["builds"], 1)
        self.assertEqual(stats["index_size"], 4)
        self.assertFalse(stats["stale"])
        self.assertTrue(stats["index_ready"])
        self.assertIsNotNone(stats["last_build_ms"])
        self.assertEqual(stats["fallback_scans"], 0)

    def test_stats_count_fallback_scan_when_index_unavailable(self):
        """索引建不起来（collection.get 抛错）→ build_failures +1，且本次召回记一次回退扫描。

        注意这是最坏的降级路径：BM25 构建与 jieba 扫描都要读 collection，向量库本身不可用时
        关键词召回为空（返回空列表而不是抛异常），计数器如实记下原因。
        """
        with patch.object(self.service.collection, "get", side_effect=RuntimeError("boom")):
            res = asyncio.run(self.service._keyword_multi_recall(
                ["货款支付"], where=None, candidate_limit=5,
            ))
        self.assertEqual(res, [])
        stats = self.service.bm25_stats()
        self.assertEqual(stats["build_failures"], 1)
        self.assertEqual(stats["fallback_scans"], 1)
        self.assertFalse(stats["index_ready"])

    def test_stats_count_import_failure_as_fallback(self):
        with patch.dict(sys.modules, {"rank_bm25": None}):
            self._run(self._rows4(), ["货款支付"], where=None)
        self.assertEqual(self.service.bm25_stats()["fallback_scans"], 1)

    def test_stats_count_ttl_rebuild(self):
        """索引超龄 → ttl_rebuilds +1 并真的重建（built_at 前移）。"""
        self._run(self._rows4(), ["货款支付"], where=None)
        with patch("app.services.rag.rag_service.settings.RAG_BM25_TTL_SECONDS", 10):
            self.service._bm25_built_at = 0.0
            self._run(self._rows4(), ["货款支付"], where=None)
        self.assertEqual(self.service.bm25_stats()["ttl_rebuilds"], 1)

    def test_stats_shape_is_json_serializable(self):
        """接口直接返回这个 dict，值必须能 JSON 序列化（built_at 为 ISO 字符串或 None）。"""
        import json

        payload = json.dumps(self.service.bm25_stats())
        self.assertIn("fallback_scans", payload)
        self.assertIn("ttl_seconds", payload)


if __name__ == "__main__":
    unittest.main()
