import asyncio
import json
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.document import DocumentQARecord
from app.core.llm_client import LLMClient
from app.models.llm_call_log import LLMCallLog
from app.models.prompt import PromptTemplate, PromptTemplateVersion
from app.services.analytics_service import analytics_service
from app.services.llm_observability_service import LLMObservabilityService
from app.services.rag_service import rag_service


class RagServiceTests(unittest.TestCase):
    def test_compact_metadata_removes_none_values(self):
        metadata = rag_service._compact_metadata(
            {
                "document_id": 1,
                "chunk_id": None,
                "chunk_index": 0,
                "page_number": None,
                "section_title": "正文",
                "embedding_id": "doc1_chunk0",
                "user_id": 2,
            }
        )

        self.assertNotIn("chunk_id", metadata)
        self.assertNotIn("page_number", metadata)
        self.assertEqual(metadata["section_title"], "正文")
        self.assertEqual(metadata["user_id"], 2)

    def test_answer_refuses_when_confidence_is_low(self):
        low_relevance_chunks = [
            {
                "id": "doc1_chunk0",
                "content": "本文仅说明服务器部署步骤、端口配置和日志采集方案，与人员安排、合同主体或责任归属无关。",
                "metadata": {
                    "chunk_id": 11,
                    "chunk_index": 0,
                    "page_number": 1,
                    "section_title": "部署说明",
                    "embedding_id": "doc1_chunk0",
                },
                "distance": 1.95,
            }
        ] 

        with patch.object(rag_service, "search_async", new=AsyncMock(return_value=low_relevance_chunks)), patch(
            "app.services.rag_service.llm_client.generate",
            new=AsyncMock(return_value="根据当前文档内容，无法确认该问题。"),
        ):
            result = asyncio.run(rag_service.answer_async("合同负责人是谁", document_id=1, user_id=1))

        self.assertFalse(result["can_answer"])
        self.assertLess(result["confidence"], 0.35)
        self.assertIn("无法确认", result["answer"])
        self.assertEqual(result["refusal_reason"], "low_confidence")
        self.assertEqual(result["citations"][0]["page_number"], 1)
        self.assertEqual(result["citations"][0]["section_title"], "部署说明")

    def test_answer_returns_citation_structure_when_confident(self):
        relevant_chunks = [
            {
                "id": "doc2_chunk0",
                "content": "付款条款：甲方应于2026年7月1日前支付首付款100万元。",
                "metadata": {
                    "chunk_id": 22,
                    "chunk_index": 0,
                    "page_number": 3,
                    "section_title": "付款条款",
                    "embedding_id": "doc2_chunk0",
                },
                "distance": 0.12,
            }
        ]

        with patch.object(rag_service, "search_async", new=AsyncMock(return_value=relevant_chunks)), patch(
            "app.services.rag_service.llm_client.generate",
            new=AsyncMock(return_value="首付款应于2026年7月1日前支付100万元。[片段 1]"),
        ):
            result = asyncio.run(rag_service.answer_async("首付款金额和时间是什么", document_id=2, user_id=1))

        self.assertTrue(result["can_answer"])
        self.assertGreaterEqual(result["confidence"], 0.35)
        self.assertIsNone(result["refusal_reason"])
        self.assertEqual(result["citations"][0]["chunk_id"], 22)
        self.assertIsNone(result["citations"][0]["document_id"])
        self.assertEqual(result["citations"][0]["page_number"], 3)
        self.assertEqual(result["citations"][0]["section_title"], "付款条款")
        self.assertIn("page:3", result["citations"][0]["locator"])
        self.assertIsNone(result["citations"][0]["visual_evidence"])
        self.assertIn("100万元", result["answer"])
        self.assertIn("runtime_config", result)
        self.assertEqual(result["runtime_config"]["top_k"], 5)

    def test_answer_refuses_when_no_retrieval_hits(self):
        with patch.object(rag_service, "search_async", new=AsyncMock(return_value=[])):
            result = asyncio.run(rag_service.answer_async("合同负责人是谁", document_id=1, user_id=1))

        self.assertFalse(result["can_answer"])
        self.assertEqual(result["refusal_reason"], "no_retrieval_hits")

    def test_answer_refuses_when_answer_not_grounded_by_chunks(self):
        relevant_chunks = [
            {
                "id": "doc2_chunk0",
                "content": "付款条款：甲方应于2026年7月1日前支付首付款100万元。",
                "metadata": {
                    "document_id": 2,
                    "chunk_id": 22,
                    "chunk_index": 0,
                    "page_number": 3,
                    "section_title": "付款条款",
                    "embedding_id": "doc2_chunk0",
                },
                "distance": 0.12,
            }
        ]

        with patch.object(rag_service, "search_async", new=AsyncMock(return_value=relevant_chunks)), patch(
            "app.services.rag_service.llm_client.generate",
            new=AsyncMock(return_value="项目团队由三名顾问和两名法务组成。"),
        ):
            result = asyncio.run(rag_service.answer_async("首付款金额和时间是什么", document_id=2, user_id=1))

        self.assertFalse(result["can_answer"])
        self.assertEqual(result["refusal_reason"], "insufficient_citation_grounding")
        self.assertLessEqual(result["confidence"], 0.3)

    def test_rewrite_queries_generates_compact_variants(self):
        variants = rag_service._rewrite_queries("请帮我看看这份文档里，首付款金额和支付时间是什么？")

        self.assertGreaterEqual(len(variants), 2)
        self.assertEqual(variants[0], "请帮我看看这份文档里，首付款金额和支付时间是什么？")
        self.assertTrue(any("首付款" in item for item in variants))

    def test_runtime_config_uses_defaults_and_guards_bounds(self):
        config = rag_service.get_runtime_config(
            top_k=0,
            confidence_threshold=2,
            min_recall_candidates=1,
            recall_multiplier=0,
            query_variant_limit=0,
            context_neighbor_window=-1,
            context_max_chunks=0,
        )

        self.assertEqual(config["top_k"], 1)
        self.assertEqual(config["confidence_threshold"], 1.0)
        self.assertEqual(config["min_recall_candidates"], 1)
        self.assertEqual(config["recall_multiplier"], 1)
        self.assertEqual(config["query_variant_limit"], 1)
        self.assertEqual(config["context_neighbor_window"], 0)
        self.assertEqual(config["context_max_chunks"], 1)

    def test_rewrite_queries_respects_limit(self):
        variants = rag_service._rewrite_queries("请帮我看看这份文档里，首付款金额和支付时间是什么？", limit=2)

        self.assertLessEqual(len(variants), 2)

    def test_rerank_candidates_merges_dense_and_keyword_routes(self):
        dense_candidates = [
            {
                "id": "doc1_chunk0",
                "content": "付款条款：甲方应于2026年7月1日前支付首付款100万元。",
                "metadata": {"chunk_id": 1, "section_title": "付款条款", "page_number": 3},
                "distance": 0.12,
                "dense_score": 0.94,
                "keyword_score": 0.0,
                "routes": {"dense"},
                "matched_variants": {"首付款金额 支付时间"},
            }
        ]
        keyword_candidates = [
            {
                "id": "doc1_chunk0",
                "content": "付款条款：甲方应于2026年7月1日前支付首付款100万元。",
                "metadata": {"chunk_id": 1, "section_title": "付款条款", "page_number": 3},
                "distance": None,
                "dense_score": 0.0,
                "keyword_score": 0.88,
                "routes": {"keyword"},
                "matched_variants": {"首付款金额 支付时间", "首付款100万元"},
            },
            {
                "id": "doc1_chunk1",
                "content": "验收标准包括接口响应时间不高于2.5秒。",
                "metadata": {"chunk_id": 2, "section_title": "验收标准", "page_number": 5},
                "distance": None,
                "dense_score": 0.0,
                "keyword_score": 0.22,
                "routes": {"keyword"},
                "matched_variants": {"支付时间"},
            },
        ]

        reranked = rag_service._rerank_candidates(
            query="首付款金额和支付时间是什么",
            query_variants=["首付款金额和支付时间是什么", "首付款金额 支付时间", "首付款100万元"],
            dense_candidates=dense_candidates,
            keyword_candidates=keyword_candidates,
            top_k=2,
        )

        self.assertEqual(reranked[0]["id"], "doc1_chunk0")
        self.assertEqual(reranked[0]["retrieval_routes"], ["dense", "keyword"])
        self.assertGreater(reranked[0]["retrieval_score"], reranked[1]["retrieval_score"])

    def test_keyword_multi_recall_uses_section_title_and_content(self):
        fake_rows = {
            "ids": ["doc1_chunk0", "doc1_chunk1"],
            "documents": [
                "甲方应于2026年7月1日前支付首付款100万元。",
                "项目团队由1名项目经理和2名后端工程师组成。",
            ],
            "metadatas": [
                {"section_title": "付款条款", "chunk_id": 1},
                {"section_title": "资源安排", "chunk_id": 2},
            ],
        }

        async def run_case():
            with patch.object(rag_service.collection, "get", return_value=fake_rows):
                return await rag_service._keyword_multi_recall(
                    ["首付款金额 支付时间", "付款条款"],
                    where={"document_id": 1},
                    candidate_limit=5,
                )

        candidates = asyncio.run(run_case())

        self.assertEqual(candidates[0]["id"], "doc1_chunk0")
        self.assertEqual(candidates[0]["routes"], {"keyword"})
        self.assertGreater(candidates[0]["keyword_score"], 0.5)

    def test_dense_multi_recall_passes_user_id_to_embedding(self):
        captured = {}

        async def fake_embed(texts, user_id=None, action="embedding"):
            captured["texts"] = texts
            captured["user_id"] = user_id
            captured["action"] = action
            return [[0.1, 0.2]]

        def fake_query(query_embeddings, n_results, where):
            captured["query_embeddings"] = query_embeddings
            captured["n_results"] = n_results
            captured["where"] = where
            return {
                "ids": [["doc1_chunk0"]],
                "documents": [["首付款 100 万元"]],
                "metadatas": [[{"document_id": 1, "chunk_index": 0}]],
                "distances": [[0.2]],
            }

        async def run_case():
            with patch("app.services.rag_service.llm_client.embed", side_effect=fake_embed), patch.object(
                rag_service.collection,
                "query",
                side_effect=fake_query,
            ):
                return await rag_service._dense_multi_recall(
                    ["首付款金额"],
                    where={"$and": [{"document_id": 1}, {"user_id": 7}]},
                    candidate_limit=3,
                    user_id=7,
                )

        candidates = asyncio.run(run_case())

        self.assertEqual(captured["user_id"], 7)
        self.assertEqual(captured["action"], "embedding")
        self.assertEqual(captured["n_results"], 3)
        self.assertEqual(candidates[0]["id"], "doc1_chunk0")
        self.assertEqual(candidates[0]["routes"], {"dense"})

    def test_index_document_embeds_visual_summary_enriched_content(self):
        captured = {}

        async def fake_embed(texts, user_id=None, action="embedding"):
            captured["texts"] = texts
            return [[0.1, 0.2]]

        with patch("app.services.rag_service.llm_client.embed", new=fake_embed), patch.object(
            rag_service.collection,
            "delete",
            return_value=None,
        ), patch.object(
            rag_service.collection,
            "add",
            return_value=None,
        ):
            rag_service.index_document(
                9,
                [
                    {
                        "chunk_index": 0,
                        "content": "本页包含甲乙双方签字和公司公章。",
                        "index_content": "[视觉摘要] 视觉标签: 公章、签字\n本页包含甲乙双方签字和公司公章。",
                        "embedding_id": "doc9_chunk0",
                        "section_title": "第 12 页",
                        "section_path": ["合同附件", "第 12 页"],
                        "segment_type": "page_ocr",
                        "visual_tags": ["seal_present", "signature_present"],
                        "ocr_quality": 0.91,
                    }
                ],
                user_id=3,
            )

        self.assertIn("[视觉摘要]", captured["texts"][0])
        self.assertIn("公司公章", captured["texts"][0])

    def test_keyword_multi_recall_boosts_section_path_match(self):
        fake_rows = {
            "ids": ["doc1_chunk0", "doc1_chunk1"],
            "documents": [
                "本节说明付款审批前的通用背景信息。",
                "这里描述项目启动会安排，与付款无关。",
            ],
            "metadatas": [
                {"section_title": "审批说明", "section_path": "合同总则 > 付款审批 > 节点要求", "chunk_id": 1},
                {"section_title": "项目安排", "section_path": "项目管理 > 启动计划", "chunk_id": 2},
            ],
        }

        async def run_case():
            with patch.object(rag_service.collection, "get", return_value=fake_rows):
                return await rag_service._keyword_multi_recall(
                    ["付款审批节点"],
                    where={"document_id": 1},
                    candidate_limit=5,
                )

        candidates = asyncio.run(run_case())

        self.assertEqual(candidates[0]["id"], "doc1_chunk0")
        self.assertGreater(candidates[0]["keyword_score"], 0.25)

    def test_keyword_multi_recall_matches_visual_aliases_for_signature_page(self):
        fake_rows = {
            "ids": ["doc1_chunk0", "doc1_chunk1"],
            "documents": [
                "本页包含甲乙双方签字和公司公章。",
                "这里描述项目背景介绍。",
            ],
            "metadatas": [
                {"section_title": "第 12 页", "section_path": "合同附件 > 第 12 页", "segment_type": "page_ocr", "page_number": 12, "chunk_id": 1, "visual_tags": "ocr visual scanned_page seal_present signature_present"},
                {"section_title": "项目背景", "section_path": "正文 > 项目背景", "segment_type": "paragraph", "chunk_id": 2},
            ],
        }

        async def run_case():
            with patch.object(rag_service.collection, "get", return_value=fake_rows):
                return await rag_service._keyword_multi_recall(
                    ["签字页公章内容", "第12页签章"],
                    where={"document_id": 1},
                    candidate_limit=5,
                )

        candidates = asyncio.run(run_case())

        self.assertEqual(candidates[0]["id"], "doc1_chunk0")
        self.assertGreater(candidates[0]["keyword_score"], 0.3)

    def test_keyword_multi_recall_matches_table_capture_aliases(self):
        fake_rows = {
            "ids": ["doc1_chunk0", "doc1_chunk1"],
            "documents": [
                "| 日期 | 金额 |\n| 2026-07-01 | 100万 |",
                "本节为普通文字说明。",
            ],
            "metadatas": [
                {"section_title": "付款计划", "section_path": "商务条款 > 付款计划", "segment_type": "table", "table_like": True, "chunk_id": 1, "visual_tags": "table_visual table_dense"},
                {"section_title": "付款说明", "section_path": "商务条款 > 付款说明", "segment_type": "paragraph", "table_like": False, "chunk_id": 2},
            ],
        }

        async def run_case():
            with patch.object(rag_service.collection, "get", return_value=fake_rows):
                return await rag_service._keyword_multi_recall(
                    ["表格截图里的付款金额", "表格 金额 日期"],
                    where={"document_id": 1},
                    candidate_limit=5,
                )

        candidates = asyncio.run(run_case())

        self.assertEqual(candidates[0]["id"], "doc1_chunk0")
        self.assertGreater(candidates[0]["keyword_score"], 0.35)

    def test_keyword_multi_recall_matches_visual_region_aliases(self):
        fake_rows = {
            "ids": ["doc1_chunk0", "doc1_chunk1"],
            "documents": [
                "本页下部包含甲方签字和乙方公章。",
                "这里描述合同背景说明。",
            ],
            "metadatas": [
                {
                    "section_title": "第 9 页",
                    "section_path": "合同附件 > 第 9 页",
                    "segment_type": "page_ocr",
                    "page_number": 9,
                    "chunk_id": 1,
                    "visual_tags": "ocr visual scanned_page seal_present signature_present",
                    "visual_region": "bottom",
                },
                {
                    "section_title": "项目背景",
                    "section_path": "正文 > 项目背景",
                    "segment_type": "paragraph",
                    "chunk_id": 2,
                },
            ],
        }

        async def run_case():
            with patch.object(rag_service.collection, "get", return_value=fake_rows):
                return await rag_service._keyword_multi_recall(
                    ["页面下部有没有签字", "下部 签字 公章"],
                    where={"document_id": 1},
                    candidate_limit=5,
                )

        candidates = asyncio.run(run_case())

        self.assertEqual(candidates[0]["id"], "doc1_chunk0")
        self.assertGreater(candidates[0]["keyword_score"], 0.3)

    def test_rerank_candidates_boosts_table_like_chunks_for_payment_queries(self):
        dense_candidates = [
            {
                "id": "doc1_chunk0",
                "content": "付款方式如下表所示。",
                "metadata": {
                    "chunk_id": 1,
                    "section_title": "付款计划",
                    "section_path": "商务条款 > 付款计划",
                    "table_like": True,
                    "segment_type": "table",
                    "visual_tags": "table_visual table_dense",
                },
                "distance": 0.35,
                "dense_score": 0.7,
                "keyword_score": 0.0,
                "routes": {"dense"},
                "matched_variants": {"付款金额 付款日期"},
            }
        ]
        keyword_candidates = [
            {
                "id": "doc1_chunk0",
                "content": "付款方式如下表所示。",
                "metadata": {
                    "chunk_id": 1,
                    "section_title": "付款计划",
                    "section_path": "商务条款 > 付款计划",
                    "table_like": True,
                    "segment_type": "table",
                    "visual_tags": "table_visual table_dense",
                },
                "distance": None,
                "dense_score": 0.0,
                "keyword_score": 0.58,
                "routes": {"keyword"},
                "matched_variants": {"付款金额 付款日期"},
            },
            {
                "id": "doc1_chunk1",
                "content": "付款相关说明：首付款在合同签订后支付。",
                "metadata": {
                    "chunk_id": 2,
                    "section_title": "付款说明",
                    "section_path": "商务条款 > 付款说明",
                    "table_like": False,
                    "segment_type": "paragraph",
                },
                "distance": None,
                "dense_score": 0.0,
                "keyword_score": 0.62,
                "routes": {"keyword"},
                "matched_variants": {"付款金额 付款日期"},
            },
        ]

        reranked = rag_service._rerank_candidates(
            query="付款金额和付款日期是什么",
            query_variants=["付款金额和付款日期是什么", "付款金额 付款日期"],
            dense_candidates=dense_candidates,
            keyword_candidates=keyword_candidates,
            top_k=2,
        )

        self.assertEqual(reranked[0]["id"], "doc1_chunk0")
        self.assertIn("structure_score", reranked[0])
        self.assertGreater(reranked[0]["structure_score"], reranked[1]["structure_score"])

    def test_rerank_candidates_boosts_ocr_chunks_for_scanned_page_queries(self):
        dense_candidates = [
            {
                "id": "doc1_chunk0",
                "content": "这是扫描页 OCR 提取的盖章页说明。",
                "metadata": {
                    "chunk_id": 1,
                    "section_title": "第 8 页",
                    "section_path": "合同附件 > 第 8 页",
                    "segment_type": "page_ocr",
                    "table_like": False,
                    "visual_tags": "ocr visual scanned_page seal_present",
                    "ocr_quality": 0.92,
                },
                "distance": 0.4,
                "dense_score": 0.68,
                "keyword_score": 0.0,
                "routes": {"dense"},
                "matched_variants": {"第8页 扫描件 盖章"},
            }
        ]
        keyword_candidates = [
            {
                "id": "doc1_chunk0",
                "content": "这是扫描页 OCR 提取的盖章页说明。",
                "metadata": {
                    "chunk_id": 1,
                    "section_title": "第 8 页",
                    "section_path": "合同附件 > 第 8 页",
                    "segment_type": "page_ocr",
                    "table_like": False,
                    "visual_tags": "ocr visual scanned_page seal_present",
                    "ocr_quality": 0.92,
                },
                "distance": None,
                "dense_score": 0.0,
                "keyword_score": 0.45,
                "routes": {"keyword"},
                "matched_variants": {"第8页 扫描件 盖章"},
            },
            {
                "id": "doc1_chunk1",
                "content": "这里描述常规交付安排。",
                "metadata": {
                    "chunk_id": 2,
                    "section_title": "交付安排",
                    "section_path": "正文 > 交付安排",
                    "segment_type": "paragraph",
                    "table_like": False,
                },
                "distance": None,
                "dense_score": 0.0,
                "keyword_score": 0.52,
                "routes": {"keyword"},
                "matched_variants": {"第8页 盖章"},
            },
        ]

        reranked = rag_service._rerank_candidates(
            query="扫描件第8页的盖章内容是什么",
            query_variants=["扫描件第8页的盖章内容是什么", "第8页 扫描件 盖章"],
            dense_candidates=dense_candidates,
            keyword_candidates=keyword_candidates,
            top_k=2,
        )

        self.assertEqual(reranked[0]["id"], "doc1_chunk0")
        self.assertGreater(reranked[0]["structure_score"], reranked[1]["structure_score"])

    def test_rerank_candidates_boosts_visual_region_matched_chunk(self):
        dense_candidates = [
            {
                "id": "doc1_chunk0",
                "content": "本页下部包含签字和公章。",
                "metadata": {
                    "chunk_id": 1,
                    "section_title": "第 9 页",
                    "section_path": "合同附件 > 第 9 页",
                    "segment_type": "page_ocr",
                    "table_like": False,
                    "visual_tags": "ocr visual scanned_page seal_present signature_present",
                    "visual_region": "bottom",
                    "ocr_quality": 0.88,
                },
                "distance": 0.46,
                "dense_score": 0.66,
                "keyword_score": 0.0,
                "routes": {"dense"},
                "matched_variants": {"页面下部 签字 公章"},
            }
        ]
        keyword_candidates = [
            {
                "id": "doc1_chunk0",
                "content": "本页下部包含签字和公章。",
                "metadata": {
                    "chunk_id": 1,
                    "section_title": "第 9 页",
                    "section_path": "合同附件 > 第 9 页",
                    "segment_type": "page_ocr",
                    "table_like": False,
                    "visual_tags": "ocr visual scanned_page seal_present signature_present",
                    "visual_region": "bottom",
                    "ocr_quality": 0.88,
                },
                "distance": None,
                "dense_score": 0.0,
                "keyword_score": 0.36,
                "routes": {"keyword"},
                "matched_variants": {"页面下部 签字 公章"},
            },
            {
                "id": "doc1_chunk1",
                "content": "本页包含签约说明。",
                "metadata": {
                    "chunk_id": 2,
                    "section_title": "签约说明",
                    "section_path": "正文 > 签约说明",
                    "segment_type": "paragraph",
                    "table_like": False,
                },
                "distance": None,
                "dense_score": 0.0,
                "keyword_score": 0.43,
                "routes": {"keyword"},
                "matched_variants": {"签字 公章"},
            },
        ]

        reranked = rag_service._rerank_candidates(
            query="页面下部有没有签字和公章",
            query_variants=["页面下部有没有签字和公章", "页面下部 签字 公章"],
            dense_candidates=dense_candidates,
            keyword_candidates=keyword_candidates,
            top_k=2,
        )

        self.assertEqual(reranked[0]["id"], "doc1_chunk0")
        self.assertGreater(reranked[0]["structure_score"], reranked[1]["structure_score"])

    def test_rerank_candidates_boosts_visual_tagged_attachment_page(self):
        dense_candidates = [
            {
                "id": "doc1_chunk0",
                "content": "本附件页包含签字与盖章内容。",
                "metadata": {
                    "chunk_id": 1,
                    "section_title": "第 12 页",
                    "section_path": "合同附件 > 第 12 页",
                    "segment_type": "page_ocr",
                    "table_like": False,
                    "visual_tags": "ocr visual scanned_page attachment_like seal_present signature_present",
                    "ocr_quality": 0.9,
                },
                "distance": 0.5,
                "dense_score": 0.64,
                "keyword_score": 0.0,
                "routes": {"dense"},
                "matched_variants": {"附件页 签字 公章"},
            }
        ]
        keyword_candidates = [
            {
                "id": "doc1_chunk0",
                "content": "本附件页包含签字与盖章内容。",
                "metadata": {
                    "chunk_id": 1,
                    "section_title": "第 12 页",
                    "section_path": "合同附件 > 第 12 页",
                    "segment_type": "page_ocr",
                    "table_like": False,
                    "visual_tags": "ocr visual scanned_page attachment_like seal_present signature_present",
                    "ocr_quality": 0.9,
                },
                "distance": None,
                "dense_score": 0.0,
                "keyword_score": 0.38,
                "routes": {"keyword"},
                "matched_variants": {"附件页 签字 公章"},
            },
            {
                "id": "doc1_chunk1",
                "content": "本节说明签约背景。",
                "metadata": {
                    "chunk_id": 2,
                    "section_title": "项目背景",
                    "section_path": "正文 > 项目背景",
                    "segment_type": "paragraph",
                    "table_like": False,
                },
                "distance": None,
                "dense_score": 0.0,
                "keyword_score": 0.41,
                "routes": {"keyword"},
                "matched_variants": {"签字"},
            },
        ]

        reranked = rag_service._rerank_candidates(
            query="附件页有没有公章和签字",
            query_variants=["附件页有没有公章和签字", "附件页 签字 公章"],
            dense_candidates=dense_candidates,
            keyword_candidates=keyword_candidates,
            top_k=2,
        )

        self.assertEqual(reranked[0]["id"], "doc1_chunk0")
        self.assertGreater(reranked[0]["structure_score"], reranked[1]["structure_score"])

    def test_search_async_runs_multi_recall_and_returns_reranked_chunks(self):
        async def run_case():
            with patch.object(
                rag_service,
                "_dense_multi_recall",
                new=AsyncMock(
                    return_value=[
                        {
                            "id": "doc1_chunk0",
                            "content": "付款条款：甲方应于2026年7月1日前支付首付款100万元。",
                            "metadata": {"chunk_id": 1, "section_title": "付款条款"},
                            "distance": 0.1,
                            "dense_score": 0.95,
                            "keyword_score": 0.0,
                            "routes": {"dense"},
                            "matched_variants": {"首付款金额 支付时间"},
                        }
                    ]
                ),
            ), patch.object(
                rag_service,
                "_keyword_multi_recall",
                new=AsyncMock(
                    return_value=[
                        {
                            "id": "doc1_chunk0",
                            "content": "付款条款：甲方应于2026年7月1日前支付首付款100万元。",
                            "metadata": {"chunk_id": 1, "section_title": "付款条款"},
                            "distance": None,
                            "dense_score": 0.0,
                            "keyword_score": 0.82,
                            "routes": {"keyword"},
                            "matched_variants": {"首付款金额 支付时间"},
                        }
                    ]
                ),
            ):
                return await rag_service.search_async("首付款金额和支付时间是什么", document_id=1, top_k=3, user_id=7)

        chunks = asyncio.run(run_case())

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["id"], "doc1_chunk0")
        self.assertEqual(chunks[0]["retrieval_routes"], ["dense", "keyword"])
        self.assertGreater(chunks[0]["retrieval_score"], 0.6)

    def test_expand_context_chunks_includes_adjacent_chunks(self):
        hit_chunks = [
            {
                "id": "doc1_chunk1",
                "content": "付款安排见相邻片段。",
                "metadata": {
                    "document_id": 1,
                    "chunk_id": 2,
                    "chunk_index": 1,
                    "page_number": 3,
                    "section_title": "付款条款",
                    "embedding_id": "doc1_chunk1",
                },
                "distance": 0.15,
                "retrieval_score": 0.82,
                "retrieval_routes": ["dense", "keyword"],
                "dense_score": 0.9,
                "keyword_score": 0.75,
                "structure_score": 0.4,
                "matched_variants": ["付款金额", "付款时间"],
            }
        ]
        fake_rows = {
            "ids": ["doc1_chunk0", "doc1_chunk1", "doc1_chunk2"],
            "documents": [
                "上一片段说明付款背景。",
                "付款安排见相邻片段。",
                "甲方应于2026年7月1日前支付首付款100万元。",
            ],
            "metadatas": [
                {"document_id": 1, "chunk_id": 1, "chunk_index": 0, "page_number": 3, "section_title": "付款条款"},
                {"document_id": 1, "chunk_id": 2, "chunk_index": 1, "page_number": 3, "section_title": "付款条款"},
                {"document_id": 1, "chunk_id": 3, "chunk_index": 2, "page_number": 3, "section_title": "付款条款"},
            ],
        }

        async def run_case():
            with patch.object(rag_service.collection, "get", return_value=fake_rows):
                return await rag_service._expand_context_chunks(
                    hit_chunks,
                    document_id=1,
                    user_id=7,
                    neighbor_window=1,
                    max_chunks=5,
                )

        expanded = asyncio.run(run_case())

        self.assertEqual([chunk["id"] for chunk in expanded], ["doc1_chunk0", "doc1_chunk1", "doc1_chunk2"])
        self.assertEqual(expanded[1]["retrieval_score"], 0.82)
        self.assertEqual(expanded[2]["retrieval_routes"], ["context_expand"])

    def test_answer_async_uses_expanded_context_for_prompt_and_grounding(self):
        retrieved_chunks = [
            {
                "id": "doc2_chunk0",
                "content": "付款条款：首付款金额和支付时间详见相邻片段。",
                "metadata": {
                    "document_id": 2,
                    "chunk_id": 22,
                    "chunk_index": 0,
                    "page_number": 3,
                    "section_title": "付款条款",
                    "embedding_id": "doc2_chunk0",
                },
                "distance": 0.1,
            }
        ]
        expanded_chunks = [
            retrieved_chunks[0],
            {
                "id": "doc2_chunk1",
                "content": "甲方应于2026年7月1日前支付首付款100万元。",
                "metadata": {
                    "document_id": 2,
                    "chunk_id": 23,
                    "chunk_index": 1,
                    "page_number": 3,
                    "section_title": "付款条款",
                    "embedding_id": "doc2_chunk1",
                },
                "distance": None,
                "retrieval_routes": ["context_expand"],
            },
        ]
        captured = {}

        def fake_render(template_name, **kwargs):
            captured["template_name"] = template_name
            captured["context"] = kwargs["context"]
            return "PROMPT"

        with patch.object(rag_service, "search_async", new=AsyncMock(return_value=retrieved_chunks)), patch.object(
            rag_service,
            "_expand_context_chunks",
            new=AsyncMock(return_value=expanded_chunks),
        ), patch(
            "app.services.rag_service.prompt_service.render_by_name",
            side_effect=fake_render,
        ), patch(
            "app.services.rag_service.llm_client.generate",
            new=AsyncMock(return_value="首付款应于2026年7月1日前支付100万元。"),
        ):
            result = asyncio.run(rag_service.answer_async("首付款金额和时间是什么", document_id=2, user_id=1))

        self.assertTrue(result["can_answer"])
        self.assertEqual(len(result["hit_chunks"]), 1)
        self.assertEqual(len(result["context_chunks"]), 2)
        self.assertEqual(result["observability"]["context_chunk_count"], 2)
        self.assertEqual(captured["template_name"], "rag_answer")
        self.assertIn("甲方应于2026年7月1日前支付首付款100万元。", captured["context"])
        self.assertIn("section:付款条款", captured["context"])

    def test_build_prompt_context_includes_path_and_segment_type(self):
        context = rag_service._build_prompt_context(
            [
                {
                    "id": "doc3_chunk0",
                    "content": "扫描页中的公章说明。",
                    "metadata": {
                        "page_number": 8,
                        "section_title": "第 8 页",
                        "section_path": "合同附件 > 第 8 页",
                        "segment_type": "page_ocr",
                        "visual_tags": "ocr visual scanned_page seal_present",
                        "visual_evidence": "本页加盖公章确认",
                        "visual_region": "bottom",
                        "chunk_index": 0,
                    },
                }
            ]
        )

        self.assertIn("path:合同附件 > 第 8 页", context)
        self.assertIn("type:page_ocr", context)
        self.assertIn("tags:ocr visual scanned_page seal_present", context)
        self.assertIn("[视觉摘要]", context)
        self.assertIn("[视觉证据]", context)
        self.assertIn("region:bottom", context)
        self.assertIn("公章", context)

    def test_build_citation_locator_includes_segment_type(self):
        locator = rag_service._build_citation_locator(
            {
                "document_id": 3,
                "page_number": 8,
                "section_title": "第 8 页",
                "segment_type": "page_ocr",
                "visual_tags": "ocr visual scanned_page seal_present",
                "visual_evidence": "本页加盖公章确认",
                "visual_region": "bottom",
                "chunk_index": 0,
            }
        )

        self.assertIn("type:page_ocr", locator)
        self.assertIn("tags:ocr visual scanned_page seal_present", locator)
        self.assertIn("evidence:本页加盖公章确认", locator)
        self.assertIn("region:bottom", locator)

    def test_build_keyword_corpus_includes_multimodal_aliases(self):
        corpus = rag_service._build_keyword_corpus(
            "本页包含甲乙双方签字和公司公章。",
            {
                "section_title": "第 12 页",
                "section_path": "合同附件 > 第 12 页",
                "segment_type": "page_ocr",
                "page_number": 12,
                "visual_tags": "ocr visual scanned_page seal_present signature_present attachment_like",
            },
        )

        self.assertIn("签字页", corpus)
        self.assertIn("公章页", corpus)
        self.assertIn("第12页", corpus)
        self.assertIn("attachment_like", corpus)
        self.assertIn("页面下部", rag_service._build_keyword_corpus(
            "本页包含甲乙双方签字和公司公章。",
            {
                "section_title": "第 12 页",
                "section_path": "合同附件 > 第 12 页",
                "segment_type": "page_ocr",
                "page_number": 12,
                "visual_tags": "ocr visual scanned_page seal_present signature_present attachment_like",
                "visual_region": "bottom",
            },
        ))

    def test_is_answer_grounded_accepts_visual_evidence_from_metadata_corpus(self):
        grounded = rag_service._is_answer_grounded(
            "该页有公章和签字。",
            [
                {
                    "id": "doc3_chunk0",
                    "content": "本页包含甲乙双方签字和公司公章。",
                    "metadata": {
                        "section_title": "第 12 页",
                        "section_path": "合同附件 > 第 12 页",
                        "segment_type": "page_ocr",
                        "page_number": 12,
                        "visual_tags": "ocr visual scanned_page seal_present signature_present",
                        "ocr_quality": 0.9,
                    },
                }
            ],
            can_answer=True,
        )

        self.assertTrue(grounded)

    def test_build_citations_includes_visual_evidence(self):
        citations = rag_service._build_citations(
            [
                {
                    "id": "doc3_chunk0",
                    "content": "本页包含甲乙双方签字和公司公章。",
                    "metadata": {
                        "document_id": 3,
                        "chunk_id": 31,
                        "chunk_index": 0,
                        "page_number": 12,
                        "section_title": "第 12 页",
                        "segment_type": "page_ocr",
                        "visual_tags": "ocr visual scanned_page seal_present signature_present",
                        "visual_evidence": "甲方代表签字：张三\n乙方已盖章确认",
                        "visual_region": "bottom",
                    },
                }
            ]
        )

        self.assertEqual(citations[0]["visual_evidence"], "甲方代表签字：张三\n乙方已盖章确认")
        self.assertEqual(citations[0]["visual_region"], "bottom")


class AnalyticsObservabilityTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        self.db = self.SessionLocal()

    def tearDown(self):
        self.db.close()

    def test_llm_call_stats_aggregate_success_and_error(self):
        self.db.add_all(
            [
                LLMCallLog(
                    module_name="document",
                    action="rag_answer",
                    model_name="qwen-plus",
                    prompt_template="rag_answer",
                    prompt_version=1,
                    input_tokens=120,
                    output_tokens=40,
                    duration_ms=820,
                    status="success",
                ),
                LLMCallLog(
                    module_name="agent",
                    action="agent_plan",
                    model_name="qwen-plus",
                    prompt_template="agent_system_prompt",
                    prompt_version=2,
                    input_tokens=80,
                    output_tokens=0,
                    duration_ms=430,
                    status="error",
                    error_message="timeout",
                ),
            ]
        )
        self.db.commit()

        stats = analytics_service.get_llm_call_stats(self.db, days=30)

        self.assertEqual(stats["total_calls"], 2)
        self.assertEqual(stats["failed_calls"], 1)
        self.assertEqual(stats["total_input_tokens"], 200)
        self.assertEqual(stats["total_output_tokens"], 40)
        self.assertEqual(stats["by_module"]["document"], 1)
        self.assertEqual(stats["by_module"]["agent"], 1)
        self.assertEqual(stats["by_action"]["agent_plan"]["failed"], 1)

    def test_llm_call_stats_include_failed_trend(self):
        self.db.add_all(
            [
                LLMCallLog(
                    module_name="document",
                    action="rag_answer",
                    model_name="qwen-plus",
                    input_tokens=30,
                    output_tokens=10,
                    duration_ms=120,
                    status="error",
                ),
                LLMCallLog(
                    module_name="document",
                    action="embedding",
                    model_name="text-embedding-v3",
                    input_tokens=20,
                    output_tokens=0,
                    duration_ms=80,
                    status="success",
                ),
            ]
        )
        self.db.commit()

        stats = analytics_service.get_llm_call_stats(self.db, days=30)

        self.assertEqual(sum(stats["failed_by_date"].values()), 1)

    def test_llm_call_stats_include_pipeline_stage_aggregates(self):
        self.db.add_all(
            [
                LLMCallLog(
                    module_name="document",
                    action="rag_pipeline",
                    model_name="qwen-plus",
                    duration_ms=480,
                    status="success",
                    response_excerpt=json.dumps(
                        {
                            "retrieval_duration_ms": 120,
                            "rerank_duration_ms": 60,
                            "generation_duration_ms": 300,
                            "result_status": "answered",
                        },
                        ensure_ascii=False,
                    ),
                ),
                LLMCallLog(
                    module_name="document",
                    action="rag_pipeline",
                    model_name="qwen-plus",
                    duration_ms=210,
                    status="refused",
                    response_excerpt=json.dumps(
                        {
                            "retrieval_duration_ms": 110,
                            "rerank_duration_ms": 50,
                            "generation_duration_ms": 0,
                            "result_status": "refused",
                        },
                        ensure_ascii=False,
                    ),
                ),
                LLMCallLog(
                    module_name="agent",
                    action="agent_run",
                    model_name="agent_orchestrator",
                    duration_ms=900,
                    status="success",
                ),
            ]
        )
        self.db.commit()

        stats = analytics_service.get_llm_call_stats(self.db, days=30)

        self.assertEqual(stats["pipeline_stats"]["rag_pipeline_runs"], 2)
        self.assertEqual(stats["pipeline_stats"]["rag_refusal_runs"], 1)
        self.assertEqual(stats["pipeline_stats"]["agent_run_count"], 1)
        self.assertEqual(stats["stage_avg_duration_ms"]["rag_retrieval_duration_ms"], 115)
        self.assertEqual(stats["stage_avg_duration_ms"]["rag_rerank_duration_ms"], 55)
        self.assertEqual(stats["stage_avg_duration_ms"]["rag_generation_duration_ms"], 150)
        self.assertEqual(stats["stage_avg_duration_ms"]["agent_run_duration_ms"], 900)

    def test_llm_billing_stats_aggregate_priced_and_unpriced_models(self):
        self.db.add_all(
            [
                LLMCallLog(
                    module_name="document",
                    action="rag_answer",
                    model_name="qwen-plus",
                    input_tokens=1000,
                    output_tokens=500,
                    status="success",
                ),
                LLMCallLog(
                    module_name="document",
                    action="embedding",
                    model_name="unknown-model",
                    input_tokens=300,
                    output_tokens=0,
                    status="success",
                ),
            ]
        )
        self.db.commit()

        stats = analytics_service.get_llm_billing_stats(self.db, days=30)

        self.assertEqual(stats["summary"]["metered_calls"], 1)
        self.assertEqual(stats["summary"]["unpriced_calls"], 1)
        self.assertAlmostEqual(stats["summary"]["total_input_cost"], 0.004, places=6)
        self.assertAlmostEqual(stats["summary"]["total_output_cost"], 0.006, places=6)
        self.assertAlmostEqual(stats["summary"]["total_cost"], 0.01, places=6)
        self.assertIn("unknown-model", stats["summary"]["unmapped_models"])
        self.assertIn("qwen-plus", stats["by_model"])

        rag_action = stats["by_action"]["rag_answer"]
        self.assertEqual(rag_action["calls"], 1)
        self.assertEqual(rag_action["priced_calls"], 1)
        self.assertEqual(rag_action["input_tokens"], 1000)
        self.assertEqual(rag_action["output_tokens"], 500)
        self.assertAlmostEqual(rag_action["total_cost"], 0.01, places=6)
        self.assertAlmostEqual(rag_action["avg_cost_per_call"], 0.01, places=6)
        self.assertAlmostEqual(
            rag_action["by_model"]["qwen-plus"]["total_cost"], 0.01, places=6
        )

        embed_action = stats["by_action"]["embedding"]
        self.assertEqual(embed_action["calls"], 1)
        self.assertEqual(embed_action["priced_calls"], 0)
        self.assertEqual(embed_action["total_cost"], 0.0)

    def test_llm_routing_stats_report_fallback_cost_and_action_latency(self):
        self.db.add_all(
            [
                LLMCallLog(
                    module_name="chat",
                    action="email_polish",
                    model_name="qwen-turbo",
                    input_tokens=1000,
                    output_tokens=500,
                    duration_ms=100,
                    status="success",
                    request_id="small-success",
                    routing_role="small",
                    routing_stage="initial",
                ),
                LLMCallLog(
                    module_name="legal",
                    action="legal_consultation",
                    model_name="qwen-plus",
                    input_tokens=1000,
                    output_tokens=500,
                    duration_ms=300,
                    status="error",
                    request_id="primary-fallback",
                    routing_role="primary",
                    routing_stage="initial",
                ),
                LLMCallLog(
                    module_name="legal",
                    action="legal_consultation",
                    model_name="qwen-turbo",
                    input_tokens=500,
                    output_tokens=200,
                    duration_ms=120,
                    status="success",
                    request_id="primary-fallback",
                    routing_role="small",
                    routing_stage="fallback",
                ),
                LLMCallLog(module_name="document", action="embedding", model_name="legacy-model", status="success"),
            ]
        )
        self.db.commit()

        stats = analytics_service.get_llm_routing_stats(self.db, days=30)

        self.assertEqual(stats["routed_requests"], 2)
        self.assertEqual(stats["untracked_calls"], 1)
        self.assertEqual(stats["small_model_initial_hits"], 1)
        self.assertEqual(stats["primary_failure_count"], 1)
        self.assertEqual(stats["fallback_request_count"], 1)
        self.assertEqual(stats["fallback_success_rate"], 1)
        self.assertEqual(stats["by_action"]["legal_consultation"]["avg_attempt_latency_ms"], 210)

    def test_llm_routing_health_requires_minimum_sample_before_degrading(self):
        now = datetime.utcnow()
        self.db.add_all(
            [
                LLMCallLog(
                    module_name="legal",
                    action="legal_consultation",
                    model_name="qwen-plus",
                    status="error",
                    request_id=f"health-{index}",
                    routing_role="primary",
                    routing_stage="initial",
                    created_at=now,
                )
                for index in range(3)
            ]
        )
        self.db.commit()

        with patch("app.services.analytics_service.settings.LLM_ROUTING_ALERT_MIN_REQUESTS", 4):
            insufficient = analytics_service.get_llm_routing_health(self.db)
        with patch("app.services.analytics_service.settings.LLM_ROUTING_ALERT_MIN_REQUESTS", 3), patch(
            "app.services.analytics_service.settings.LLM_ROUTING_ALERT_PRIMARY_FAILURE_RATE", 0.5
        ):
            degraded = analytics_service.get_llm_routing_health(self.db)

        self.assertEqual(insufficient["status"], "ok")
        self.assertEqual(insufficient["warnings"], [])
        self.assertEqual(degraded["status"], "degraded")
        self.assertEqual(degraded["warnings"], ["primary_failure_rate_high"])

    def test_list_qa_replays_returns_structured_payload(self):
        self.db.add(
            DocumentQARecord(
                document_id=1,
                user_id=1,
                question="付款时间是什么？",
                answer="2026-07-01。",
                source="document",
                citations=json.dumps([{"page_number": 3, "source_text": "应于2026年7月1日前支付。"}], ensure_ascii=False),
                hit_chunks=json.dumps([{"chunk_id": 11, "content": "应于2026年7月1日前支付。"}], ensure_ascii=False),
                feedback_status="open",
            )
        )
        self.db.commit()

        payload = analytics_service.list_qa_replays(self.db, user_id=1, days=30)

        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["citations"][0]["page_number"], 3)
        self.assertEqual(payload["items"][0]["hit_chunks"][0]["chunk_id"], 11)
        self.assertEqual(payload["items"][0]["feedback_status"], "open")

    def test_feedback_stats_aggregate_value_status_reason_and_resolution(self):
        self.db.add_all(
            [
                DocumentQARecord(
                    document_id=1,
                    user_id=1,
                    question="付款时间是什么？",
                    answer="2026-07-01。",
                    source="document",
                    feedback_value="negative",
                    feedback_reason="wrong_citation",
                    feedback_note="引用片段不对",
                    feedback_status="open",
                    feedback_created_at=datetime.utcnow(),
                ),
                DocumentQARecord(
                    document_id=1,
                    user_id=1,
                    question="首付款金额是什么？",
                    answer="100万元。",
                    source="chat",
                    feedback_value="positive",
                    feedback_status="resolved",
                    feedback_created_at=datetime.utcnow(),
                    feedback_resolved_at=datetime.utcnow(),
                ),
                DocumentQARecord(
                    document_id=1,
                    user_id=1,
                    question="交付节点是什么？",
                    answer="2026-08-01。",
                    source="document",
                    feedback_value="negative",
                    feedback_reason="not_helpful",
                    feedback_status="resolved",
                    feedback_created_at=datetime.utcnow(),
                    feedback_resolved_at=datetime.utcnow(),
                ),
            ]
        )
        self.db.commit()

        stats = analytics_service.get_feedback_stats(self.db, user_id=1, days=30)

        self.assertEqual(stats["total_feedback"], 3)
        self.assertEqual(stats["positive_count"], 1)
        self.assertEqual(stats["negative_count"], 2)
        self.assertEqual(stats["open_count"], 1)
        self.assertEqual(stats["resolved_count"], 2)
        self.assertEqual(stats["by_reason"]["wrong_citation"], 1)
        self.assertEqual(stats["by_source"]["document"], 2)
        self.assertEqual(stats["by_source"]["chat"], 1)
        self.assertEqual(stats["resolution_rate"], 0.5)

    def test_experiment_overview_returns_empty_artifact_state_when_outputs_missing(self):
        with TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            baseline_path = output_dir / "baseline_snapshot.json"
            with patch("app.services.analytics_service.DEFAULT_OUTPUT_DIR", output_dir), patch(
                "app.services.analytics_service.DEFAULT_BASELINE_SNAPSHOT_PATH",
                baseline_path,
            ):
                overview = analytics_service.get_experiment_overview(self.db, days=30)

        self.assertFalse(overview["artifact_status"]["summary"]["exists"])
        self.assertFalse(overview["artifact_status"]["baseline_snapshot"]["exists"])
        self.assertEqual(overview["summary"]["experiment_count"], 0)
        self.assertEqual(overview["rollouts"]["active_rollout_count"], 0)

    def test_experiment_overview_aggregates_eval_artifacts_rollouts_and_prompt_traffic(self):
        template = PromptTemplate(
            name="rag_answer",
            variables="question,context",
            rollout_percentage=20,
            rollout_started_at=datetime.utcnow(),
        )
        self.db.add(template)
        self.db.commit()
        self.db.refresh(template)
        v1 = PromptTemplateVersion(
            template_id=template.id,
            version=1,
            template="v1",
            is_active=True,
            change_note="stable",
        )
        v2 = PromptTemplateVersion(
            template_id=template.id,
            version=2,
            template="v2",
            is_active=False,
            change_note="candidate",
        )
        self.db.add_all([v1, v2])
        self.db.commit()
        self.db.refresh(v1)
        self.db.refresh(v2)
        template.active_version_id = v1.id
        template.previous_active_version_id = v1.id
        template.rollout_version_id = v2.id
        self.db.add(template)
        self.db.add(
            LLMCallLog(
                module_name="document",
                action="rag_answer",
                model_name="qwen-plus",
                prompt_template="rag_answer",
                prompt_version=2,
                status="success",
                input_tokens=20,
                output_tokens=5,
            )
        )
        self.db.commit()

        summary_payload = {
            "dataset_size": 32,
            "experiment_count": 2,
            "baseline_experiment": "baseline",
            "bundle_meta": {"bundle_name": "demo"},
            "experiments": [
                {
                    "name": "baseline",
                    "effective_config": {"top_k": 5, "prompt_template": "rag_answer", "prompt_version": 1},
                    "summary": {"hit_at_k": 1.0, "citation_accuracy": 0.93, "refusal_accuracy": 1.0},
                    "baseline_delta": {"hit_at_k": 0.0, "citation_accuracy": 0.0, "refusal_accuracy": 0.0, "badcase_count": 0},
                    "badcase_count": 1,
                    "badcase_path": "eval/outputs/baseline_badcases.json",
                },
                {
                    "name": "gray_v2",
                    "effective_config": {"top_k": 5, "prompt_template": "rag_answer", "prompt_version": 2},
                    "summary": {"hit_at_k": 0.97, "citation_accuracy": 0.91, "refusal_accuracy": 1.0},
                    "baseline_delta": {"hit_at_k": -0.03, "citation_accuracy": -0.02, "refusal_accuracy": 0.0, "badcase_count": 1},
                    "badcase_count": 2,
                    "badcase_path": "eval/outputs/gray_v2_badcases.json",
                },
            ],
        }
        baseline_payload = {
            "baseline_experiment": "baseline",
            "baseline": {
                "effective_config": {"top_k": 5, "prompt_template": "rag_answer", "prompt_version": 1},
                "summary": {"hit_at_k": 1.0, "citation_accuracy": 0.93, "refusal_accuracy": 1.0},
            },
        }
        with TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            summary_path = output_dir / "summary.json"
            baseline_path = output_dir / "baseline_snapshot.json"
            summary_path.write_text(json.dumps(summary_payload, ensure_ascii=False), encoding="utf-8")
            baseline_path.write_text(json.dumps(baseline_payload, ensure_ascii=False), encoding="utf-8")
            with patch("app.services.analytics_service.DEFAULT_OUTPUT_DIR", output_dir), patch(
                "app.services.analytics_service.DEFAULT_BASELINE_SNAPSHOT_PATH",
                baseline_path,
            ):
                overview = analytics_service.get_experiment_overview(self.db, days=30)

        self.assertTrue(overview["artifact_status"]["summary"]["exists"])
        self.assertEqual(overview["summary"]["experiment_count"], 2)
        self.assertEqual(overview["summary"]["degraded_experiment_count"], 1)
        self.assertEqual(overview["rollouts"]["active_rollout_count"], 1)
        self.assertEqual(overview["prompt_traffic"]["items"][0]["prompt_version"], 2)
        degraded = next(item for item in overview["experiments"] if item["name"] == "gray_v2")
        self.assertIn("citation_accuracy", degraded["regression_metrics"])
        self.assertTrue(any(item["field"] == "prompt_version" for item in degraded["config_drift"]))

    def test_llm_observability_service_redacts_sensitive_request_response_and_error(self):
        service = LLMObservabilityService()

        with patch("app.services.llm_observability_service.SessionLocal", self.SessionLocal):
            service.log_event(
                module_name="chat",
                action="chat",
                model_name="qwen-plus",
                status="error",
                user_id=7,
                error_message="api_key=secret-value",
                request_excerpt={"messages": [{"role": "user", "content": "合同全文"}]},
                response_excerpt="模型返回的全文内容",
            )

        row = self.db.query(LLMCallLog).one()
        request_payload = json.loads(row.request_excerpt)
        response_payload = json.loads(row.response_excerpt)
        error_payload = json.loads(row.error_message)
        self.assertTrue(request_payload["redacted"])
        self.assertEqual(request_payload["action"], "chat")
        self.assertTrue(response_payload["redacted"])
        self.assertEqual(response_payload["kind"], "response")
        self.assertTrue(error_payload["redacted"])
        self.assertEqual(error_payload["kind"], "error")

    def test_llm_observability_service_keeps_embedding_excerpt_readable(self):
        service = LLMObservabilityService()

        with patch("app.services.llm_observability_service.SessionLocal", self.SessionLocal):
            service.log_event(
                module_name="document",
                action="embedding",
                model_name="text-embedding-v3",
                request_excerpt={"input_count": 2, "sample": ["第一段", "第二段"]},
                response_excerpt="embedding_count=2",
            )

        row = self.db.query(LLMCallLog).one()
        self.assertEqual(row.request_excerpt, '{"input_count": 2, "sample": ["第一段", "第二段"]}')
        self.assertEqual(row.response_excerpt, "embedding_count=2")


class EmbeddingObservabilityTests(unittest.IsolatedAsyncioTestCase):
    def test_sanitize_observability_excerpt_redacts_sensitive_actions(self):
        client = LLMClient()

        sanitized_request = client._sanitize_observability_excerpt(
            "chat",
            '[{"role":"user","content":"合同正文"}]',
            kind="request",
        )
        sanitized_response = client._sanitize_observability_excerpt(
            "generate_with_images",
            "图片中包含签字和公章",
            kind="response",
        )

        request_payload = json.loads(sanitized_request)
        response_payload = json.loads(sanitized_response)
        self.assertTrue(request_payload["redacted"])
        self.assertEqual(request_payload["action"], "chat")
        self.assertEqual(request_payload["kind"], "request")
        self.assertGreater(request_payload["length"], 0)
        self.assertTrue(response_payload["redacted"])
        self.assertEqual(response_payload["action"], "generate_with_images")
        self.assertEqual(response_payload["kind"], "response")

    def test_sanitize_observability_excerpt_keeps_embedding_summary(self):
        client = LLMClient()

        sanitized = client._sanitize_observability_excerpt(
            "embedding",
            '{"input_count":2,"sample":["第一段","第二段"]}',
            kind="request",
        )

        self.assertEqual(sanitized, '{"input_count":2,"sample":["第一段","第二段"]}')

    async def test_answer_async_returns_observability_payload_and_logs_pipeline(self):
        relevant_chunks = [
            {
                "id": "doc2_chunk0",
                "content": "The upfront payment is 1 million CNY before 2026-07-01.",
                "metadata": {
                    "chunk_id": 22,
                    "chunk_index": 0,
                    "page_number": 3,
                    "section_title": "Payment Terms",
                    "embedding_id": "doc2_chunk0",
                },
                "distance": 0.12,
            }
        ]
        captured = {}

        def fake_log_event(**kwargs):
            captured.update(kwargs)

        with patch.object(rag_service, "search_async", new=AsyncMock(return_value=relevant_chunks)), patch(
            "app.services.rag_service.llm_client.generate",
            new=AsyncMock(return_value="The upfront payment is 1 million CNY before 2026-07-01."),
        ), patch(
            "app.services.rag_service.llm_observability_service.log_event",
            side_effect=fake_log_event,
        ):
            result = await rag_service.answer_async("What is the upfront payment amount?", document_id=2, user_id=1)

        self.assertIn("observability", result)
        self.assertEqual(result["observability"]["result_status"], "answered")
        self.assertGreaterEqual(result["observability"]["retrieval_duration_ms"], 0)
        self.assertEqual(captured["action"], "rag_pipeline")
        self.assertEqual(captured["module_name"], "document")
        self.assertEqual(captured["status"], "answered")

    async def test_embed_records_observability_log_for_openai_compatible(self):
        fake_payload = {
            "data": [
                {"embedding": [0.1, 0.2]},
                {"embedding": [0.3, 0.4]},
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 0},
        }

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return fake_payload

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url, json=None, headers=None):
                return FakeResponse()

        with patch("app.core.llm_client.settings.LLM_PROVIDER", "openai_compatible"), patch(
            "app.core.llm_client.settings.LLM_API_BASE_URL",
            "https://example.com/v1",
        ), patch("app.core.llm_client.settings.LLM_API_KEY", "test-key"), patch(
            "app.core.llm_client.settings.LLM_MODEL",
            "qwen-plus",
        ), patch("app.core.llm_client.settings.EMBEDDING_MODEL", "text-embedding-v3"), patch(
            "app.core.llm_client.httpx.AsyncClient",
            FakeClient,
        ):
            client = LLMClient()
            captured = {}

            def fake_record_usage(
                data,
                model,
                action,
                duration_ms,
                user_id=None,
                request_excerpt=None,
                response_excerpt=None,
                error_message=None,
                status="success",
                prompt_template=None,
                prompt_version=None,
            ):
                captured.update(
                    {
                        "data": data,
                        "model": model,
                        "action": action,
                        "request_excerpt": request_excerpt,
                        "response_excerpt": response_excerpt,
                        "status": status,
                    }
                )

            with patch.object(client, "_record_usage", side_effect=fake_record_usage):
                embeddings = await client.embed(["第一段文本", "第二段文本"])

        self.assertEqual(len(embeddings), 2)
        self.assertEqual(captured["action"], "embedding")
        self.assertEqual(captured["model"], "text-embedding-v3")
        self.assertEqual(captured["status"], "success")
        self.assertEqual(captured["response_excerpt"], "embedding_count=2")
        request_excerpt = json.loads(captured["request_excerpt"])
        self.assertEqual(request_excerpt["input_count"], 2)

    async def test_embed_batches_openai_compatible_requests_by_ten(self):
        posted_batches = []

        class FakeResponse:
            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url, json=None, headers=None):
                current_batch = list(json["input"])
                posted_batches.append(current_batch)
                payload = {
                    "data": [{"embedding": [float(index), float(index) + 0.5]} for index, _ in enumerate(current_batch)],
                    "usage": {"prompt_tokens": len(current_batch), "completion_tokens": 0},
                }
                return FakeResponse(payload)

        texts = [f"第{i}段" for i in range(23)]
        with patch("app.core.llm_client.settings.LLM_PROVIDER", "openai_compatible"), patch(
            "app.core.llm_client.settings.LLM_API_BASE_URL",
            "https://example.com/v1",
        ), patch("app.core.llm_client.settings.LLM_API_KEY", "test-key"), patch(
            "app.core.llm_client.settings.LLM_MODEL",
            "qwen-plus",
        ), patch("app.core.llm_client.settings.EMBEDDING_MODEL", "text-embedding-v3"), patch(
            "app.core.llm_client.httpx.AsyncClient",
            FakeClient,
        ):
            client = LLMClient()
            with patch.object(client, "_record_usage"):
                embeddings = await client.embed(texts)

        self.assertEqual([len(batch) for batch in posted_batches], [10, 10, 3])
        self.assertEqual(len(embeddings), 23)

    def test_build_chat_payload_supports_multimodal_messages(self):
        client = LLMClient()

        payload = client._build_chat_payload(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "请识别图片中的公章"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                    ],
                }
            ],
            stream=False,
            temperature=0.2,
        )

        self.assertEqual(payload["messages"][0]["content"][0]["type"], "text")
        self.assertEqual(payload["messages"][0]["content"][1]["type"], "image_url")
        self.assertEqual(
            payload["messages"][0]["content"][1]["image_url"]["url"],
            "data:image/png;base64,abc",
        )

    def test_build_multimodal_generate_payload_uses_vision_model(self):
        with patch("app.core.llm_client.settings.LLM_VISION_MODEL", "qwen-vl-plus"):
            client = LLMClient()

        payload = client._build_multimodal_generate_payload(
            prompt="请识别图片中的签字",
            image_urls=["https://example.com/signature.png"],
            temperature=0.3,
        )

        self.assertEqual(payload["model"], "qwen-vl-plus")
        self.assertEqual(payload["messages"][0]["content"][0]["type"], "text")
        self.assertEqual(payload["messages"][0]["content"][1]["type"], "image_url")

    async def test_generate_with_images_records_usage_for_openai_compatible(self):
        fake_payload = {
            "choices": [{"message": {"content": "图片中包含签字和公章。"}}],
            "usage": {"prompt_tokens": 18, "completion_tokens": 6},
        }

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return fake_payload

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url, json=None, headers=None):
                return FakeResponse()

        with patch("app.core.llm_client.settings.LLM_PROVIDER", "openai_compatible"), patch(
            "app.core.llm_client.settings.LLM_API_BASE_URL",
            "https://example.com/v1",
        ), patch("app.core.llm_client.settings.LLM_API_KEY", "test-key"), patch(
            "app.core.llm_client.settings.LLM_MODEL",
            "qwen-plus",
        ), patch("app.core.llm_client.settings.LLM_VISION_MODEL", "qwen-vl-plus"), patch(
            "app.core.llm_client.httpx.AsyncClient",
            FakeClient,
        ):
            client = LLMClient()
            captured = {}

            def fake_record_usage(
                data,
                model,
                action,
                duration_ms,
                user_id=None,
                request_excerpt=None,
                response_excerpt=None,
                error_message=None,
                status="success",
                prompt_template=None,
                prompt_version=None,
            ):
                captured.update(
                    {
                        "model": model,
                        "action": action,
                        "request_excerpt": request_excerpt,
                        "response_excerpt": response_excerpt,
                        "status": status,
                    }
                )

            with patch.object(client, "_record_usage", side_effect=fake_record_usage):
                result = await client.generate_with_images(
                    "请识别图片中的签字",
                    image_urls=["https://example.com/signature.png"],
                )

        self.assertEqual(result, "图片中包含签字和公章。")
        self.assertEqual(captured["model"], "qwen-vl-plus")
        self.assertEqual(captured["action"], "generate_with_images")
        self.assertEqual(captured["status"], "success")


if __name__ == "__main__":
    unittest.main()
