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


if __name__ == "__main__":
    unittest.main()
