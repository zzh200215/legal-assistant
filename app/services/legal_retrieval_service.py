"""Hybrid article retrieval for the legal knowledge base.

Lexical recall remains available for every deployment.  Dense recall is an
enhancement: failures to reach the embedding provider or vector store never
hide legal sources that can be found by exact citations or keywords.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections import defaultdict

import jieba
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.llm_client import LLMClient, llm_client
from app.models.legal import LegalArticle, LegalSource
from app.services.legal_knowledge_graph_service import legal_knowledge_graph_service
from app.services.rag_cache import rag_embedding_cache
from app.services.rerank import LLMReranker
from app.services.vector_store import build_vector_store

logger = logging.getLogger(__name__)
settings = get_settings()

ARTICLE_CITATION_PATTERN = re.compile(r"第[一二三四五六七八九十百零\d]+条")


def _tokens(text: str) -> set[str]:
    return {
        word.strip().lower()
        for word in jieba.cut(text or "")
        if len(word.strip()) >= 2 and re.search(r"[一-鿿A-Za-z]", word)
    }


def _article_vector_id(article_id: int) -> str:
    """Qdrant accepts UUIDs while Chroma accepts them as ordinary string IDs."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"legal-article:{article_id}"))


def _graph_support_boost(support_count: int | float | None) -> float:
    """Return a bounded corroboration boost without dominating base retrieval."""
    try:
        normalized = max(0, int(support_count or 0))
    except (TypeError, ValueError):
        normalized = 0
    capped = min(normalized, settings.LEGAL_GRAPH_EVIDENCE_MAX_SUPPORT_COUNT)
    return settings.LEGAL_GRAPH_EVIDENCE_BOOST * capped


class LegalRetrievalService:
    def __init__(self, *, client: LLMClient | None = None, collection=None, graph_service=None) -> None:
        self.client = client or LLMClient()
        self.collection = collection
        self.graph_service = graph_service or legal_knowledge_graph_service

    def _collection(self):
        if self.collection is None:
            self.collection = build_vector_store(settings.LEGAL_VECTOR_STORE_COLLECTION_NAME).collection
        return self.collection

    async def index_source(self, db: Session, source_id: int, user_id: int) -> int:
        """Replace the source's article vectors after a reviewed import/update."""
        rows = (
            db.query(LegalArticle, LegalSource)
            .join(LegalSource, LegalArticle.source_id == LegalSource.id)
            .filter(LegalArticle.source_id == source_id, LegalSource.user_id == user_id)
            .order_by(LegalArticle.sequence)
            .all()
        )
        collection = self._collection()
        try:
            collection.delete(where={"source_id": source_id, "user_id": user_id})
        except Exception:
            # No collection/old vectors is normal on the first index.
            pass
        if not rows:
            return 0

        documents = [
            f"{source.title} {article.article_number} {article.title or ''}\n{article.content}"
            for article, source in rows
        ]
        if settings.RAG_EMBED_CACHE_ENABLED:
            # RAG① 内容寻址：未变文章命中缓存不再重算嵌入
            embeddings = await rag_embedding_cache.get_or_compute_batch(
                documents,
                lambda ms: self.client.embed(ms, user_id=user_id, action="embedding"),
            )
        else:
            embeddings = await self.client.embed(documents, user_id=user_id, action="embedding")
        if len(embeddings) != len(rows):
            raise RuntimeError("embedding result count does not match legal articles")
        collection.add(
            ids=[_article_vector_id(article.id) for article, _ in rows],
            embeddings=embeddings,
            documents=documents,
            metadatas=[
                {
                    "article_id": article.id,
                    "source_id": source.id,
                    "user_id": user_id,
                    "status": source.status,
                    "article_number": article.article_number,
                }
                for article, source in rows
            ],
        )
        return len(rows)

    async def delete_source(self, source_id: int, user_id: int) -> None:
        try:
            self._collection().delete(where={"source_id": source_id, "user_id": user_id})
        except Exception as exc:
            logger.warning("Unable to remove legal vectors for source %s: %s", source_id, type(exc).__name__)

    async def search(self, db: Session, query: str, user_id: int, limit: int = 20) -> list[dict]:
        """Fuse lexical and dense article recalls with reciprocal-rank fusion."""
        rows = (
            db.query(LegalArticle, LegalSource)
            .join(LegalSource, LegalArticle.source_id == LegalSource.id)
            .filter(LegalSource.user_id == user_id)
            .filter(LegalSource.status != "inactive")
            .all()
        )
        if not rows:
            return []

        # E-7：先把检索数据物化为纯 dict——LLM 调用前结束事务归还 DB 连接，
        # 避免连接在等待期间被占用，也避免依赖 ORM 对象的 session 生命周期。
        article_data = [
            {
                "article_id": article.id,
                "source_id": source.id,
                "source_title": source.title,
                "citation": source.citation or "",
                "article_number": article.article_number,
                "title": article.title,
                "content": article.content,
                "chapter": article.chapter,
                "sequence": article.sequence,
            }
            for article, source in rows
        ]
        db.rollback()

        query_tokens = _tokens(query)
        citations = set(ARTICLE_CITATION_PATTERN.findall(query))
        lexical = []
        for item in article_data:
            haystack = (
                f"{item['source_title']} {item['citation']} {item['article_number']} "
                f"{item['title'] or ''} {item['content']}"
            ).lower()
            keyword_hits = sum(1 for token in query_tokens if token in haystack)
            citation_hit = item["article_number"] in citations
            if keyword_hits or citation_hit:
                lexical.append((item["article_id"], citation_hit * 10 + keyword_hits, keyword_hits, citation_hit))
        lexical.sort(key=lambda item: item[1], reverse=True)

        dense_ids: list[int] = []
        try:
            if settings.RAG_EMBED_CACHE_ENABLED:
                query_embeddings = await rag_embedding_cache.get_or_compute_batch(
                    [query],
                    lambda ms: self.client.embed(ms, user_id=user_id, action="embedding"),
                )
                embedding = query_embeddings[0]
            else:
                embedding = (await self.client.embed([query], user_id=user_id, action="embedding"))[0]
            n_results = max(
                limit * settings.LEGAL_DENSE_RECALL_MULTIPLIER,
                settings.LEGAL_DENSE_MIN_CANDIDATES,
            )
            result = await asyncio.to_thread(
                self._collection().query,
                query_embeddings=[embedding],
                n_results=n_results,
                where={"user_id": user_id, "status": {"$ne": "inactive"}},
            )
            for metadata in (result.get("metadatas") or [[]])[0]:
                article_id = (metadata or {}).get("article_id")
                if article_id is not None:
                    dense_ids.append(int(article_id))
        except Exception as exc:
            logger.info("Dense legal recall unavailable; using lexical recall (%s)", type(exc).__name__)

        # RRF combines independent rankings without assuming scores are calibrated.
        fused = defaultdict(float)
        details: dict[int, dict] = defaultdict(lambda: {"keyword_hits": 0, "citation_hit": False, "dense_rank": None})
        for rank, (article_id, _, keyword_hits, citation_hit) in enumerate(lexical, start=1):
            fused[article_id] += 1 / (60 + rank)
            details[article_id].update(keyword_hits=keyword_hits, citation_hit=citation_hit)
        for rank, article_id in enumerate(dense_ids, start=1):
            fused[article_id] += 1 / (60 + rank)
            details[article_id]["dense_rank"] = rank

        # The graph only corroborates existing lexical/vector candidates. It
        # never introduces a new source, so a graph outage cannot alter base recall.
        graph_evidence = await self.graph_service.relation_evidence(
            user_id=user_id,
            article_ids=list(fused),
        )
        for article_id, evidence in graph_evidence.items():
            if article_id not in fused:
                continue
            boost = _graph_support_boost(evidence.get("support_count"))
            fused[article_id] += boost
            details[article_id]["graph_support"] = {**evidence, "boost": round(boost, 6)}

        by_id = {item["article_id"]: item for item in article_data}
        ranked_ids = sorted(fused, key=lambda article_id: fused[article_id], reverse=True)[:limit]
        results = [
            {
                "id": by_id[article_id]["article_id"],
                "source_id": by_id[article_id]["source_id"],
                "source_title": by_id[article_id]["source_title"],
                "article_number": by_id[article_id]["article_number"],
                "title": by_id[article_id]["title"],
                "content": by_id[article_id]["content"][:300] + ("..." if len(by_id[article_id]["content"]) > 300 else ""),
                "chapter": by_id[article_id]["chapter"],
                "sequence": by_id[article_id]["sequence"],
                "score": round(fused[article_id], 6),
                "score_breakdown": details[article_id],
            }
            for article_id in ranked_ids
        ]
        return await self._maybe_llm_rerank(query, results, user_id)

    async def _maybe_llm_rerank(self, query: str, ranked: list[dict], user_id: int) -> list[dict]:
        """RAG④：开启 LLM 重排时对融合 top-N 用 qwen-plus 打分重排；失败回退融合顺序。"""
        if not settings.RAG_LLM_RERANK_ENABLED or len(ranked) < 2:
            return ranked
        top_n = ranked[: settings.RAG_LLM_RERANK_TOP_N]
        if len(top_n) < 2:
            return ranked
        try:
            prompt = self._build_llm_rerank_prompt(query, top_n)
            response = await llm_client.generate(
                prompt, temperature=0.0, action="rag_rerank", user_id=user_id,
            )
            scores = LLMReranker._parse_scores(response)
            reordered = LLMReranker._apply_scores(top_n, scores)
            return (reordered + ranked[len(top_n):])[: len(ranked)]
        except Exception as exc:  # noqa: BLE001 - 重排失败不阻断检索
            logger.warning("Legal LLM rerank failed; keeping fused order (%s)", type(exc).__name__)
            return ranked

    @staticmethod
    def _build_llm_rerank_prompt(query: str, candidates: list[dict]) -> str:
        lines = []
        for index, candidate in enumerate(candidates):
            snippet = (candidate.get("content") or "")[: settings.RAG_LLM_RERANK_MAX_CHARS]
            lines.append(f"[{index}] {snippet}")
        return (
            "你是法律检索相关性评判。请按与问题的相关程度给每个法条片段打分（0-10 整数，越高越相关）。"
            "只输出 JSON：{\"scores\":[<int>,...]}，不要输出其他内容。\n"
            f"问题：{query}\n"
            + "\n".join(lines)
        )


legal_retrieval_service = LegalRetrievalService()
