"""Unit tests for LegalRetrievalService — hybrid lexical + dense article search.

Tests run entirely in-process: in-memory SQLite, ephemeral Chroma collection,
and mocked embed calls.  No external services, no disk writes.
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import chromadb
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers all ORM metadata
from app.core.database import Base
from app.models.legal import LegalArticle, LegalSource
from app.services.legal.legal_retrieval_service import (
    LegalRetrievalService,
    _article_vector_id,
    _graph_support_boost,
    _like_escape,
)
from app.services.rag.vector_store import ChromaVectorStoreCollection

USER_ID = 1
EMBED_DIM = 4  # tiny deterministic vectors


def _make_engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _fake_source(db, *, title, citation="", status="active", user_id=USER_ID):
    src = LegalSource(
        user_id=user_id, title=title, source_type="statute",
        citation=citation, jurisdiction="中国大陆", version="v1",
        status=status, content=title,
    )
    db.add(src)
    db.flush()
    return src


def _fake_article(db, source_id, *, article_number, content, sequence=1, chapter=None):
    art = LegalArticle(
        source_id=source_id, article_number=article_number,
        content=content, sequence=sequence, chapter=chapter,
    )
    db.add(art)
    db.flush()
    return art


def _add_to_collection(collection, article_id, source_id, *, user_id=USER_ID, status="active"):
    """Add a deterministic embedding for an article to the test Chroma collection."""
    vector = [float(article_id) * 0.1, 0.2, 0.3, 0.4]
    collection.add(
        ids=[_article_vector_id(article_id)],
        embeddings=[vector],
        documents=[f"article {article_id}"],
        metadatas=[{"article_id": article_id, "source_id": source_id,
                    "user_id": user_id, "status": status, "article_number": "第1条"}],
    )


class LegalRetrievalServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        engine = _make_engine()
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        self.db = Session()

        chroma_client = chromadb.EphemeralClient()
        raw_collection = chroma_client.get_or_create_collection("test_legal_articles")
        self.collection = ChromaVectorStoreCollection(raw_collection)

        self._make_service()

    def _make_service(self, embed_side_effect=None):
        fake_llm = MagicMock()
        fake_llm.embed = AsyncMock(
            return_value=[[0.0, 0.0, 0.0, 0.0]] if embed_side_effect is None else None,
            side_effect=embed_side_effect,
        )
        self.fake_llm = fake_llm
        self.service = LegalRetrievalService(client=fake_llm, collection=self.collection)

    def tearDown(self):
        self.db.close()

    async def test_empty_db_returns_empty(self):
        results = await self.service.search(self.db, "经济补偿", user_id=USER_ID)
        self.assertEqual(results, [])

    async def test_lexical_keyword_hit_returns_matching_article(self):
        src = _fake_source(self.db, title="劳动合同法", citation="劳动合同法")
        art = _fake_article(self.db, src.id, article_number="第47条",
                            content="经济补偿按劳动者在本单位工作年限计算")
        self.db.commit()

        results = await self.service.search(self.db, "经济补偿工作年限", user_id=USER_ID)
        self.assertGreater(len(results), 0)
        ids = [r["id"] for r in results]
        self.assertIn(art.id, ids)

    async def test_search_uses_embedding_cache(self):
        """RAG①：同一查询两次，查询嵌入只算一次（第二次命中缓存）。"""
        src = _fake_source(self.db, title="劳动合同法", citation="劳动合同法")
        _fake_article(self.db, src.id, article_number="第47条", content="经济补偿按工作年限计算")
        self.db.commit()

        await self.service.search(self.db, "经济补偿", user_id=USER_ID)
        await self.service.search(self.db, "经济补偿", user_id=USER_ID)
        self.assertEqual(self.fake_llm.embed.call_count, 1)

    async def test_index_source_skips_unchanged_embeddings(self):
        """RAG①：同一 source 重复索引，未变文章不重算嵌入。"""
        src = _fake_source(self.db, title="劳动合同法", citation="劳动合同法")
        _fake_article(self.db, src.id, article_number="第47条", content="经济补偿按工作年限计算")
        self.db.commit()

        await self.service.index_source(self.db, src.id, USER_ID)
        await self.service.index_source(self.db, src.id, USER_ID)
        self.assertEqual(self.fake_llm.embed.call_count, 1)

    async def test_llm_rerank_reorders_when_enabled(self):
        """RAG④：开启 LLM 重排后，融合结果按 LLM 分数重排并附 llm_rerank_score。"""
        src = _fake_source(self.db, title="劳动合同法", citation="劳动合同法")
        art1 = _fake_article(self.db, src.id, article_number="第47条", content="经济补偿按工作年限计算")
        art2 = _fake_article(self.db, src.id, article_number="第48条", content="经济补偿标准为本地区平均工资")
        self.db.commit()
        self.assertEqual(self.fake_llm.embed.call_count, 0)

        with patch("app.services.legal.legal_retrieval_service.settings.RAG_LLM_RERANK_ENABLED", True), patch(
            "app.services.legal.legal_retrieval_service.llm_client.generate",
            new=AsyncMock(return_value='{"scores": [5, 9]}'),
        ) as mock_gen:
            results = await self.service.search(self.db, "经济补偿", user_id=USER_ID)
        mock_gen.assert_awaited()
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["llm_rerank_score"], 9)

    async def test_llm_rerank_disabled_keeps_fused_order(self):
        """RAG④：默认关闭时不调用 LLM，顺序保持融合结果。"""
        src = _fake_source(self.db, title="劳动合同法", citation="劳动合同法")
        _fake_article(self.db, src.id, article_number="第47条", content="经济补偿按工作年限计算")
        self.db.commit()

        with patch("app.services.legal.legal_retrieval_service.settings.RAG_LLM_RERANK_ENABLED", False), patch(
            "app.services.legal.legal_retrieval_service.llm_client.generate",
            new=AsyncMock(),
        ) as mock_gen:
            results = await self.service.search(self.db, "经济补偿", user_id=USER_ID)
        mock_gen.assert_not_awaited()
        self.assertFalse(any("llm_rerank_score" in r for r in results))

    async def test_citation_pattern_boosts_matching_article_number(self):
        src = _fake_source(self.db, title="劳动合同法", citation="劳动合同法")
        art47 = _fake_article(self.db, src.id, article_number="第47条",
                              content="经济补偿按工作年限每年支付一个月工资", sequence=1)
        art46 = _fake_article(self.db, src.id, article_number="第46条",
                              content="用人单位依第四十条解除合同须支付经济补偿", sequence=2)
        self.db.commit()

        results = await self.service.search(self.db, "根据劳动合同法第47条经济补偿怎么算", user_id=USER_ID)
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["id"], art47.id, "Citation hit should rank first")

    async def test_pending_update_source_included(self):
        """pending_update sources are de-ranked but not excluded from recall."""
        src = _fake_source(self.db, title="劳动法", status="pending_update")
        art = _fake_article(self.db, src.id, article_number="第24条",
                            content="协商一致解除劳动合同双方")
        self.db.commit()

        results = await self.service.search(self.db, "协商一致解除劳动合同", user_id=USER_ID)
        ids = [r["id"] for r in results]
        self.assertIn(art.id, ids, "pending_update sources must be searchable")

    async def test_inactive_source_excluded(self):
        src = _fake_source(self.db, title="旧版劳动法", status="inactive")
        _fake_article(self.db, src.id, article_number="第40条", content="无过失性辞退条款（已失效）")
        self.db.commit()

        results = await self.service.search(self.db, "无过失性辞退", user_id=USER_ID)
        self.assertEqual(results, [], "inactive sources must be excluded")

    async def test_active_and_inactive_source_only_active_returned(self):
        active_src = _fake_source(self.db, title="劳动合同法", status="active")
        inactive_src = _fake_source(self.db, title="旧版劳动法", status="inactive")
        art_active = _fake_article(self.db, active_src.id, article_number="第40条",
                                    content="无过失性辞退现行规定")
        _fake_article(self.db, inactive_src.id, article_number="第40条", content="无过失性辞退旧版规定")
        self.db.commit()

        results = await self.service.search(self.db, "无过失性辞退", user_id=USER_ID)
        result_ids = [r["id"] for r in results]
        self.assertIn(art_active.id, result_ids)
        source_ids = {r["source_id"] for r in results}
        self.assertNotIn(inactive_src.id, source_ids)

    async def test_user_isolation(self):
        other_user_src = _fake_source(self.db, title="劳动合同法", user_id=999)
        _fake_article(self.db, other_user_src.id, article_number="第40条", content="经济补偿计算规则")
        self.db.commit()

        results = await self.service.search(self.db, "经济补偿", user_id=USER_ID)
        self.assertEqual(results, [], "should not see another user's sources")

    async def test_dense_failure_falls_back_to_lexical(self):
        src = _fake_source(self.db, title="消费者权益保护法")
        art = _fake_article(self.db, src.id, article_number="第55条", content="欺诈三倍赔偿消费者维权条款")
        self.db.commit()

        self._make_service(embed_side_effect=RuntimeError("embedding service unavailable"))
        results = await self.service.search(self.db, "欺诈三倍赔偿", user_id=USER_ID)
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["id"], art.id)

    async def test_dense_only_article_surfaces_when_no_lexical_match(self):
        """An article with zero lexical overlap surfaces when the dense query returns it."""
        from unittest.mock import patch

        src = _fake_source(self.db, title="民间借贷司法解释")
        art = _fake_article(self.db, src.id, article_number="第25条", content="借贷利率上限相关规定")
        self.db.commit()

        self.fake_llm.embed = AsyncMock(return_value=[[0.1, 0.2, 0.3, 0.4]])
        mock_query_result = {
            "metadatas": [[{"article_id": art.id, "source_id": src.id,
                            "user_id": USER_ID, "status": "active", "article_number": "第25条"}]]
        }
        with patch.object(self.collection, "query", return_value=mock_query_result):
            results = await self.service.search(self.db, "完全无关的查询词语", user_id=USER_ID)

        ids = [r["id"] for r in results]
        self.assertIn(art.id, ids, "dense-only article should surface via RRF")

    async def test_graph_evidence_is_added_without_changing_candidate_set(self):
        source = _fake_source(self.db, title="劳动合同法", citation="劳动合同法")
        first = _fake_article(self.db, source.id, article_number="第46条", content="解除劳动合同应支付经济补偿")
        second = _fake_article(self.db, source.id, article_number="第47条", content="经济补偿按照工作年限计算")
        self.db.commit()

        graph = MagicMock()
        graph.relation_evidence = AsyncMock(return_value={
            second.id: {
                "version_relations": ["AMENDS"],
                "related_article_ids": [first.id],
                "shared_law_area": True,
                "support_count": 2,
            }
        })
        service = LegalRetrievalService(client=self.fake_llm, collection=self.collection, graph_service=graph)

        results = await service.search(self.db, "劳动合同经济补偿", user_id=USER_ID)

        result_by_id = {item["id"]: item for item in results}
        self.assertEqual(set(result_by_id), {first.id, second.id})
        self.assertEqual(result_by_id[second.id]["score_breakdown"]["graph_support"]["support_count"], 2)
        self.assertEqual(result_by_id[second.id]["score_breakdown"]["graph_support"]["boost"], 0.002)
        graph.relation_evidence.assert_awaited_once()

    async def test_graph_evidence_only_breaks_near_ties_and_more_support_is_bounded(self):
        source = _fake_source(self.db, title="民法典合同编", citation="民法典")
        first = _fake_article(self.db, source.id, article_number="第577条", content="合同违约责任和损失赔偿")
        second = _fake_article(self.db, source.id, article_number="第585条", content="合同违约责任和损失赔偿")
        self.db.commit()

        no_graph = MagicMock()
        no_graph.relation_evidence = AsyncMock(return_value={})
        baseline_service = LegalRetrievalService(client=self.fake_llm, collection=self.collection, graph_service=no_graph)
        baseline = await baseline_service.search(self.db, "合同违约责任", user_id=USER_ID)

        graph = MagicMock()
        graph.relation_evidence = AsyncMock(return_value={
            second.id: {
                "version_relations": ["AMENDED_BY"],
                "related_article_ids": [first.id],
                "shared_law_area": True,
                "support_count": 9,
            }
        })
        graph_service = LegalRetrievalService(client=self.fake_llm, collection=self.collection, graph_service=graph)
        with_graph = await graph_service.search(self.db, "合同违约责任", user_id=USER_ID)

        self.assertEqual({item["id"] for item in baseline}, {item["id"] for item in with_graph})
        self.assertEqual(baseline[0]["id"], first.id)
        self.assertEqual(with_graph[0]["id"], second.id)
        self.assertEqual(with_graph[0]["score_breakdown"]["graph_support"]["boost"], 0.003)
        self.assertEqual(_graph_support_boost(99), _graph_support_boost(3))

    async def test_result_schema_has_required_fields(self):
        src = _fake_source(self.db, title="劳动合同法", citation="劳动合同法")
        _fake_article(self.db, src.id, article_number="第46条", content="用人单位应当向劳动者支付经济补偿")
        self.db.commit()

        results = await self.service.search(self.db, "经济补偿", user_id=USER_ID)
        self.assertGreater(len(results), 0)
        item = results[0]
        for key in ("id", "source_id", "source_title", "article_number", "content", "score", "score_breakdown"):
            self.assertIn(key, item, f"missing key: {key}")

    async def test_limit_caps_result_count(self):
        src = _fake_source(self.db, title="民法典", citation="民法典")
        for i in range(10):
            _fake_article(self.db, src.id, article_number=f"第{500 + i}条",
                          content=f"合同义务条款第{i}项内容", sequence=i)
        self.db.commit()

        results = await self.service.search(self.db, "合同义务", user_id=USER_ID, limit=3)
        self.assertLessEqual(len(results), 3)

    async def test_search_does_not_rollback_caller_transaction(self):
        """检索使用独立会话：不得 rollback 调用方未提交事务。"""
        src = _fake_source(self.db, title="劳动合同法", citation="劳动合同法")
        _fake_article(self.db, src.id, article_number="第47条", content="经济补偿按工作年限计算")
        self.db.commit()

        # 调用方未提交的独立变更（不 flush 也不 commit）
        pending = LegalSource(
            user_id=USER_ID, title="待提交法源", source_type="statute",
            jurisdiction="中国大陆", version="v1", status="active", content="待提交",
        )
        self.db.add(pending)

        results = await self.service.search(self.db, "经济补偿", user_id=USER_ID)
        self.assertGreater(len(results), 0)
        # 关键：search 不能把 pending 对象从调用方会话中清掉
        self.assertIn(pending, self.db)

    async def test_sql_prefilter_surfaces_matching_amid_many_unrelated(self):
        """SQL 侧词级预过滤：大量无关法条不进入 Python 候选，命中文章仍被召回。"""
        src = _fake_source(self.db, title="民法典", citation="民法典")
        matching = _fake_article(self.db, src.id, article_number="第47条",
                                 content="经济补偿按劳动者在本单位工作年限计算")
        for i in range(20):
            _fake_article(self.db, src.id, article_number=f"第{500 + i}条",
                          content=f"完全无关的条款内容第{i}项", sequence=i)
        self.db.commit()

        results = await self.service.search(self.db, "经济补偿工作年限", user_id=USER_ID)
        self.assertGreater(len(results), 0)
        self.assertIn(matching.id, [r["id"] for r in results])

    def test_like_escape_escapes_wildcards(self):
        self.assertEqual(_like_escape("50%_a"), "50\\%\\_a")
        self.assertEqual(_like_escape("正常词"), "正常词")
        self.assertEqual(_like_escape(None), "")


if __name__ == "__main__":
    unittest.main()
