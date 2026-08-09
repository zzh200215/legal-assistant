"""RAG④ 可插拔重排：默认启发式、开关、LLM 打分重排、失败回退。"""
import unittest
from unittest.mock import AsyncMock, patch

from app.services.rag_service import rag_service


class RerankerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.candidates = [
            {"id": "c1", "content": "甲方应支付货款一百万元", "metadata": {},
             "distance": 0.1, "rrf_score": 0.8, "dense_score": 0.7, "keyword_score": 0.9,
             "routes": {"dense", "keyword"}, "matched_variants": {"货款"}},
            {"id": "c2", "content": "劳动合同期限为三年", "metadata": {},
             "distance": 0.3, "rrf_score": 0.6, "dense_score": 0.5, "keyword_score": 0.5,
             "routes": {"dense"}, "matched_variants": {"合同"}},
        ]

    def test_build_heuristic_by_default(self):
        """默认引擎 heuristic：重排默认关闭、零依赖（BGE 需显式开启）。"""
        from app.services.rerank import HeuristicReranker, build_reranker
        with patch("app.services.rerank.settings.RAG_RERANK_ENGINE", "heuristic"), patch(
            "app.services.rerank.settings.RAG_LLM_RERANK_ENABLED", False,
        ):
            reranker = build_reranker(rag_service)
        self.assertIsInstance(reranker, HeuristicReranker)

    def test_build_bge_when_engine_bge(self):
        from app.services.rerank import BGEReranker, build_reranker
        with patch("app.services.rerank.settings.RAG_RERANK_ENGINE", "bge"), patch(
            "app.services.rerank.settings.RAG_LLM_RERANK_ENABLED", False,
        ):
            reranker = build_reranker(rag_service)
        self.assertIsInstance(reranker, BGEReranker)

    def test_build_heuristic_when_engine_heuristic(self):
        from app.services.rerank import HeuristicReranker, build_reranker
        with patch("app.services.rerank.settings.RAG_RERANK_ENGINE", "heuristic"), patch(
            "app.services.rerank.settings.RAG_LLM_RERANK_ENABLED", False,
        ):
            reranker = build_reranker(rag_service)
        self.assertIsInstance(reranker, HeuristicReranker)

    def test_build_llm_when_legacy_flag_enabled(self):
        from app.services.rerank import LLMReranker, build_reranker
        with patch("app.services.rerank.settings.RAG_LLM_RERANK_ENABLED", True):
            reranker = build_reranker(rag_service)
        self.assertIsInstance(reranker, LLMReranker)

    async def test_heuristic_delegates_to_rerank_candidates(self):
        from app.services.rerank import HeuristicReranker
        reranker = HeuristicReranker(rag_service)
        with patch.object(rag_service, "_rerank_candidates", return_value=[self.candidates[0]]) as mock_rerank:
            result = await reranker.rerank(
                query="q", query_variants=["q"], candidates=self.candidates, top_k=1,
            )
        mock_rerank.assert_called_once()
        self.assertEqual(result, [self.candidates[0]])

    async def test_llm_rerank_reorders_and_attaches_scores(self):
        from app.core.llm_client import llm_client
        from app.services.rerank import LLMReranker
        reranker = LLMReranker(rag_service)
        with patch.object(llm_client, "generate", new=AsyncMock(return_value='{"scores": [5, 9]}')) as mock_gen:
            result = await reranker.rerank(
                query="货款", query_variants=["货款"], candidates=self.candidates, top_k=2, user_id=7,
            )
        self.assertEqual(result[0]["id"], "c2")          # 9 分最高 → 排最前
        self.assertEqual(result[0]["llm_rerank_score"], 9)
        self.assertEqual(mock_gen.call_args.kwargs["action"], "rag_rerank")

    async def test_llm_rerank_failure_falls_back_to_heuristic(self):
        from app.core.llm_client import llm_client
        from app.services.rerank import LLMReranker
        reranker = LLMReranker(rag_service)
        with patch.object(llm_client, "generate", new=AsyncMock(side_effect=Exception("llm down"))):
            result = await reranker.rerank(
                query="q", query_variants=["q"], candidates=self.candidates, top_k=2, user_id=7,
            )
        self.assertTrue(result)
        self.assertNotIn("llm_rerank_score", result[0])  # 回退启发式，无 LLM 分数

    async def test_llm_rerank_bad_json_falls_back(self):
        from app.core.llm_client import llm_client
        from app.services.rerank import LLMReranker
        reranker = LLMReranker(rag_service)
        with patch.object(llm_client, "generate", new=AsyncMock(return_value="抱歉，无法评分。")):
            result = await reranker.rerank(
                query="q", query_variants=["q"], candidates=self.candidates, top_k=2, user_id=7,
            )
        self.assertTrue(result)
        self.assertNotIn("llm_rerank_score", result[0])

    async def test_bge_rerank_reorders_with_scores(self):
        from unittest.mock import MagicMock
        from app.services.rerank import BGEReranker
        reranker = BGEReranker(rag_service)
        fake_model = MagicMock()
        fake_model.predict.return_value = [0.3, 0.9]
        with patch.object(BGEReranker, "_model", fake_model):
            result = await reranker.rerank(
                query="货款", query_variants=["货款"], candidates=self.candidates, top_k=2, user_id=7,
            )
        self.assertEqual(result[0]["id"], "c2")            # 0.9 更高 → 排最前
        self.assertEqual(result[0]["bge_rerank_score"], 0.9)

    async def test_bge_rerank_falls_back_when_model_unavailable(self):
        from app.services.rerank import BGEReranker
        reranker = BGEReranker(rag_service)
        with patch.object(BGEReranker, "_load_model", return_value=None):
            result = await reranker.rerank(
                query="q", query_variants=["q"], candidates=self.candidates, top_k=2, user_id=7,
            )
        self.assertTrue(result)
        self.assertNotIn("bge_rerank_score", result[0])


if __name__ == "__main__":
    unittest.main()
