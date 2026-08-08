import unittest
from unittest.mock import AsyncMock, patch

from app.services.agentic_rag_service import AgenticRAGService


class AgenticRAGServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = AgenticRAGService()
        self.chunk = {
            "id": "doc_1_chunk_0",
            "content": "差旅报销应在出差结束后 30 日内提交申请。",
            "metadata": {"document_id": 1, "chunk_id": 1, "chunk_index": 0, "page_number": 2},
            "distance": 0.1,
        }
        self.answer = {
            "answer": "应在出差结束后 30 日内提交申请。[片段 1]",
            "citations": [],
            "hit_chunks": [self.chunk],
            "context_chunks": [self.chunk],
            "confidence": 0.8,
            "can_answer": True,
            "refusal_reason": None,
            "latency_ms": 12,
            "observability": {},
        }

    async def test_returns_after_first_round_when_evidence_is_sufficient(self):
        previous = self.service.settings.AGENTIC_RAG_PLANNER_ENABLED
        self.service.settings.AGENTIC_RAG_PLANNER_ENABLED = False
        try:
            with patch(
                "app.services.agentic_rag_service.rag_service.search_async",
                new=AsyncMock(return_value=[self.chunk]),
            ) as search, patch(
                "app.services.agentic_rag_service.rag_service._estimate_confidence",
                return_value=0.8,
            ), patch(
                "app.services.agentic_rag_service.rag_service.answer_from_chunks_async",
                new=AsyncMock(return_value=dict(self.answer)),
            ) as answer, patch(
                "app.services.agentic_rag_service.llm_observability_service.log_event",
            ):
                result = await self.service.answer_async("差旅报销多久内提交", document_id=1, user_id=7)
        finally:
            self.service.settings.AGENTIC_RAG_PLANNER_ENABLED = previous

        self.assertEqual(search.await_count, 1)
        self.assertEqual(answer.await_args.kwargs["chunks"], [self.chunk])
        self.assertTrue(result["agentic_rag"]["enabled"])
        self.assertEqual(result["agentic_rag"]["retrieval_rounds"], 1)
        self.assertEqual(result["agentic_rag"]["steps"][-1]["node"], "assess_evidence")

    async def test_threads_scope_filters_to_search_and_generate(self):
        """RAG②：knowledge_base_id / document_status 穿透到检索与生成。"""
        previous = self.service.settings.AGENTIC_RAG_PLANNER_ENABLED
        self.service.settings.AGENTIC_RAG_PLANNER_ENABLED = False
        try:
            with patch(
                "app.services.agentic_rag_service.rag_service.search_async",
                new=AsyncMock(return_value=[self.chunk]),
            ) as search, patch(
                "app.services.agentic_rag_service.rag_service._estimate_confidence",
                return_value=0.8,
            ), patch(
                "app.services.agentic_rag_service.rag_service.answer_from_chunks_async",
                new=AsyncMock(return_value=dict(self.answer)),
            ) as answer, patch(
                "app.services.agentic_rag_service.llm_observability_service.log_event",
            ):
                await self.service.answer_async(
                    "差旅报销多久内提交", document_id=1, user_id=7,
                    knowledge_base_id=3, document_status="indexed",
                )
        finally:
            self.service.settings.AGENTIC_RAG_PLANNER_ENABLED = previous

        self.assertEqual(search.await_args.kwargs.get("knowledge_base_id"), 3)
        self.assertEqual(search.await_args.kwargs.get("document_status"), "indexed")
        self.assertEqual(answer.await_args.kwargs.get("knowledge_base_id"), 3)
        self.assertEqual(answer.await_args.kwargs.get("document_status"), "indexed")

    async def test_refines_once_when_first_retrieval_is_insufficient(self):
        weak_chunk = {**self.chunk, "content": "这是其他制度说明。"}
        previous = self.service.settings.AGENTIC_RAG_PLANNER_ENABLED
        self.service.settings.AGENTIC_RAG_PLANNER_ENABLED = False
        try:
            with patch(
                "app.services.agentic_rag_service.rag_service.search_async",
                new=AsyncMock(side_effect=[[weak_chunk], [self.chunk]]),
            ) as search, patch(
                "app.services.agentic_rag_service.rag_service._estimate_confidence",
                side_effect=[0.2, 0.8],
            ), patch(
                "app.services.agentic_rag_service.rag_service.answer_from_chunks_async",
                new=AsyncMock(return_value=dict(self.answer)),
            ) as answer, patch(
                "app.services.agentic_rag_service.llm_observability_service.log_event",
            ):
                result = await self.service.answer_async("差旅报销多久内提交", document_id=1, user_id=7)
        finally:
            self.service.settings.AGENTIC_RAG_PLANNER_ENABLED = previous

        self.assertEqual(search.await_count, 2)
        self.assertEqual(answer.await_args.kwargs["chunks"], [self.chunk])
        self.assertEqual(result["agentic_rag"]["retrieval_rounds"], 2)
        self.assertIn("refine", [item["node"] for item in result["agentic_rag"]["steps"]])


if __name__ == "__main__":
    unittest.main()
