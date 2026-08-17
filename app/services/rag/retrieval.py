"""检索召回簇：混合检索（dense + BM25/jieba keyword）+ RRF 融合 + 启发式重排。

这些方法原本是 ``RAGService`` 上有状态的检索方法，以 mixin 形式抽离以削减上帝类规模。
它们依赖宿主实例的状态（``self.collection``、``self._bm25_*``）与打分委托方法
（``self._extract_query_units``、``self._distance_score`` 等，定义于 ``RAGService``），
因此仍通过 ``self.`` 访问，行为不变。
"""

import asyncio
import logging
import re
import time

from app.core.config import get_settings
from app.core.llm_client import llm_client
from app.services.rag.rag_cache import rag_embedding_cache

settings = get_settings()
logger = logging.getLogger(__name__)


class RetrievalMixin:
    def _rrf_fuse_candidates(
        self,
        *,
        dense_candidates: list[dict],
        keyword_candidates: list[dict],
        k: int = 60,
    ) -> list[dict]:
        merged: dict[str, dict] = {}
        for source_name, source_candidates in (("dense", dense_candidates), ("keyword", keyword_candidates)):
            for rank, candidate in enumerate(source_candidates, start=1):
                key = str(candidate["id"])
                current = merged.setdefault(
                    key,
                    {
                        "id": candidate["id"],
                        "content": candidate["content"],
                        "metadata": candidate.get("metadata") or {},
                        "distance": candidate.get("distance"),
                        "dense_score": 0.0,
                        "keyword_score": 0.0,
                        "routes": set(),
                        "matched_variants": set(),
                        "rrf_score": 0.0,
                    },
                )
                current["rrf_score"] += 1.0 / (k + rank)
                current["routes"].update(candidate.get("routes") or {source_name})
                current["matched_variants"].update(candidate.get("matched_variants") or set())
                current["dense_score"] = max(current["dense_score"], candidate.get("dense_score", 0.0))
                current["keyword_score"] = max(current["keyword_score"], candidate.get("keyword_score", 0.0))
                if current.get("distance") is None or (
                    candidate.get("distance") is not None and candidate["distance"] < current["distance"]
                ):
                    current["distance"] = candidate.get("distance")
        return list(merged.values())

    async def _dense_multi_recall(
        self,
        query_variants: list[str],
        *,
        where: dict | None,
        candidate_limit: int,
        user_id: int | None = None,
    ) -> list[dict]:
        if not query_variants:
            return []
        if settings.RAG_EMBED_CACHE_ENABLED:
            embeddings = await rag_embedding_cache.get_or_compute_batch(
                query_variants,
                lambda ms: llm_client.embed(ms, user_id=user_id, action="embedding"),
            )
        else:
            embeddings = await llm_client.embed(query_variants, user_id=user_id, action="embedding")
        result_sets = await asyncio.gather(
            *[
                asyncio.to_thread(
                    self.collection.query,
                    query_embeddings=[embedding],
                    n_results=candidate_limit,
                    where=where,
                )
                for embedding in embeddings
            ],
            return_exceptions=True,
        )
        merged: dict[str, dict] = {}
        for variant, result in zip(query_variants, result_sets):
            if isinstance(result, Exception):
                logger.warning(
                    "Vector store dense recall unavailable; continuing with other recall routes (%s)",
                    type(result).__name__,
                )
                continue
            ids = (result.get("ids") or [[]])[0]
            documents = (result.get("documents") or [[]])[0]
            metadatas = (result.get("metadatas") or [[]])[0]
            distances = (result.get("distances") or [[]])[0] if result.get("distances") else []
            for index, chunk_id in enumerate(ids):
                key = str(chunk_id)
                distance = distances[index] if index < len(distances) else None
                dense_score = self._distance_score(distance)
                candidate = merged.setdefault(
                    key,
                    {
                        "id": chunk_id,
                        "content": documents[index],
                        "metadata": metadatas[index] or {},
                        "distance": distance,
                        "dense_score": 0.0,
                        "keyword_score": 0.0,
                        "routes": set(),
                        "matched_variants": set(),
                    },
                )
                if candidate.get("distance") is None or (distance is not None and distance < candidate["distance"]):
                    candidate["distance"] = distance
                candidate["dense_score"] = max(candidate["dense_score"], dense_score)
                candidate["routes"].add("dense")
                candidate["matched_variants"].add(variant)
        return list(merged.values())

    def _bm25_tokenize(self, text: str) -> list[str]:
        return sorted(self._extract_query_units(text))

    def _invalidate_bm25(self) -> None:
        self._bm25_epoch += 1
        self._bm25_stale = True

    def _build_bm25_index(self) -> None:
        """懒构建内存 BM25 索引（一次全量取回，后续查询命中不再全表扫描）。

        并发锁 + epoch：同一进程内并发查询不会重复构建；构建期间失效的陈旧索引不会覆盖新数据。
        构建成功时间用于 TTL 兜底过期重建。
        """
        with self._bm25_lock:
            epoch = self._bm25_epoch
            self._bm25_items = []
            self._bm25_index = None
            try:
                rows = self.collection.get(include=["documents", "metadatas"])
            except Exception as exc:
                logger.warning("BM25 index build unavailable; falling back (%s)", type(exc).__name__)
                self._bm25_stale = True
                return
            ids = rows.get("ids") or []
            docs = rows.get("documents") or []
            metas = rows.get("metadatas") or []
            corpus_tokens = []
            for i, chunk_id in enumerate(ids):
                content = docs[i] if i < len(docs) else ""
                metadata = metas[i] if i < len(metas) else {}
                self._bm25_items.append((chunk_id, content, metadata))
                corpus_text = self._build_keyword_corpus(content, metadata)
                corpus_tokens.append(self._bm25_tokenize(corpus_text))
            if corpus_tokens:
                try:
                    from rank_bm25 import BM25Okapi
                    self._bm25_index = BM25Okapi(corpus_tokens)
                except Exception:
                    self._bm25_index = None
            # 构建期间被并发失效（epoch 已前进）：不应用陈旧索引，标记待重建
            if self._bm25_epoch != epoch:
                self._bm25_stale = True
                return
            self._bm25_stale = False
            self._bm25_built_at = time.time()

    def _bm25_keyword_recall(self, query_variants: list[str], *, where: dict | None,
                             candidate_limit: int) -> list[dict] | None:
        """BM25 关键词召回；未装 rank_bm25 / 无索引时返回 None 触发 jieba 回退。"""
        try:
            from rank_bm25 import BM25Okapi  # noqa: F401
        except ImportError:
            return None
        if self._bm25_index is None or self._bm25_stale:
            self._build_bm25_index()
        elif settings.RAG_BM25_TTL_SECONDS > 0 and time.time() - self._bm25_built_at > settings.RAG_BM25_TTL_SECONDS:
            # TTL 兜底：索引超龄（可能漏了失效信号）强制重建，避免旧数据长期参与检索
            self._bm25_stale = True
            self._build_bm25_index()
        if self._bm25_index is None:
            return None
        scored: dict[str, dict] = {}
        for variant in query_variants:
            tokens = self._bm25_tokenize(variant)
            if not tokens:
                continue
            scores = self._bm25_index.get_scores(tokens)
            for idx, raw_score in enumerate(scores):
                if idx >= len(self._bm25_items):
                    continue
                chunk_id, content, metadata = self._bm25_items[idx]
                # 全局索引必须按 where 元数据剪枝，否则跨用户/范围泄漏
                if not self._metadata_matches_where(metadata, where):
                    continue
                if raw_score <= 0:
                    continue
                entry = scored.setdefault(
                    str(chunk_id),
                    {
                        "id": chunk_id,
                        "content": content,
                        "metadata": metadata,
                        "distance": None,
                        "dense_score": 0.0,
                        "keyword_score": 0.0,
                        "routes": {"keyword"},
                        "matched_variants": set(),
                        "_raw_bm25": 0.0,
                    },
                )
                entry["_raw_bm25"] = max(entry["_raw_bm25"], float(raw_score))
                entry["matched_variants"].add(variant)
        candidates = list(scored.values())
        if not candidates:
            return []
        max_score = max(c["_raw_bm25"] for c in candidates)
        for c in candidates:
            c["keyword_score"] = (c["_raw_bm25"] / max_score) if max_score > 0 else 0.0
            c.pop("_raw_bm25", None)
        candidates.sort(key=lambda item: (item["keyword_score"], len(item["matched_variants"])), reverse=True)
        return candidates[:candidate_limit]

    async def _keyword_multi_recall(
        self,
        query_variants: list[str],
        *,
        where: dict | None,
        candidate_limit: int,
    ) -> list[dict]:
        if settings.RAG_BM25_ENABLED:
            try:
                bm25_candidates = await asyncio.to_thread(
                    self._bm25_keyword_recall,
                    query_variants,
                    where=where,
                    candidate_limit=candidate_limit,
                )
                # 非空才用 BM25；空结果（小语料下 Okapi idf 退化）回退 jieba 扫描保证召回
                if bm25_candidates:
                    return bm25_candidates
            except Exception as exc:
                logger.warning("BM25 keyword recall error; falling back to scan (%s)", type(exc).__name__)
        # 回退：jieba 全表扫描（原行为）
        try:
            rows = await asyncio.to_thread(
                self.collection.get,
                where=where,
                include=["documents", "metadatas"],
            )
        except Exception as exc:
            logger.warning(
                "Vector store keyword recall unavailable; continuing without keyword candidates (%s)",
                type(exc).__name__,
            )
            return []
        ids = rows.get("ids") or []
        documents = rows.get("documents") or []
        metadatas = rows.get("metadatas") or []
        candidates = []
        for index, chunk_id in enumerate(ids):
            content = documents[index] if index < len(documents) else ""
            metadata = metadatas[index] if index < len(metadatas) else {}
            keyword_score, matched_variants = self._keyword_match_score(query_variants, content, metadata or {})
            if keyword_score <= 0:
                continue
            candidates.append(
                {
                    "id": chunk_id,
                    "content": content,
                    "metadata": metadata or {},
                    "distance": None,
                    "dense_score": 0.0,
                    "keyword_score": keyword_score,
                    "routes": {"keyword"},
                    "matched_variants": matched_variants,
                }
            )
        candidates.sort(
            key=lambda item: (
                item["keyword_score"],
                len(item["matched_variants"]),
                len(item["content"]),
            ),
            reverse=True,
        )
        return candidates[:candidate_limit]

    def _keyword_match_score(self, query_variants: list[str], content: str, metadata: dict) -> tuple[float, set[str]]:
        corpus = self._build_keyword_corpus(content, metadata)
        best_score = 0.0
        matched_variants: set[str] = set()
        for variant in query_variants:
            terms = self._extract_query_units(variant)
            if not terms:
                continue
            matched = sum(1 for term in terms if term in corpus)
            if matched:
                matched_variants.add(variant)
            overlap_score = matched / max(len(terms), 1)
            exact_phrase_score = 1.0 if len(variant) >= 4 and variant in corpus else 0.0
            structure_score = self._structure_match_score([variant], metadata)
            best_score = max(
                best_score,
                (overlap_score * 0.65) + (exact_phrase_score * 0.2) + (structure_score * 0.15),
            )
        return min(best_score, 1.0), matched_variants

    def _rerank_candidates(
        self,
        *,
        query: str,
        query_variants: list[str],
        fused_candidates: list[dict] | None = None,
        dense_candidates: list[dict] | None = None,
        keyword_candidates: list[dict] | None = None,
        top_k: int,
    ) -> list[dict]:
        if fused_candidates is None:
            fused_candidates = self._rrf_fuse_candidates(
                dense_candidates=dense_candidates or [],
                keyword_candidates=keyword_candidates or [],
            )
        reranked = []
        for candidate in fused_candidates:
            phrase_score = self._phrase_match_score(query_variants, candidate["content"], candidate["metadata"])
            coverage_score = len(candidate["matched_variants"]) / max(len(query_variants), 1)
            structure_score = self._structure_match_score(query_variants, candidate["metadata"])
            combined_score = (
                candidate["rrf_score"] * 2.6
                + candidate["dense_score"] * 0.3
                + candidate["keyword_score"] * 0.25
                + phrase_score * 0.15
                + structure_score * 0.05
                + coverage_score * 0.05
            )
            reranked.append(
                {
                    "id": candidate["id"],
                    "content": candidate["content"],
                    "metadata": candidate["metadata"],
                    "distance": candidate["distance"],
                    "retrieval_score": round(combined_score, 4),
                    "rrf_score": round(candidate["rrf_score"], 4),
                    "retrieval_routes": sorted(candidate["routes"]),
                    "dense_score": round(candidate["dense_score"], 4),
                    "keyword_score": round(candidate["keyword_score"], 4),
                    "structure_score": round(structure_score, 4),
                    "matched_variants": sorted(candidate["matched_variants"]),
                }
            )

        reranked.sort(
            key=lambda item: (
                item["retrieval_score"],
                item["rrf_score"],
                item["keyword_score"],
                item["dense_score"],
                -(item["distance"] if item["distance"] is not None else 999),
            ),
            reverse=True,
        )
        return reranked[:top_k]

    def _phrase_match_score(self, query_variants: list[str], content: str, metadata: dict) -> float:
        corpus = self._build_keyword_corpus(content, metadata)
        best = 0.0
        for variant in query_variants:
            compact = re.sub(r"\s+", "", variant)
            if len(compact) < 4:
                continue
            if compact in corpus or variant in corpus:
                best = 1.0
                break
        return best

    def _build_keyword_corpus(self, content: str, metadata: dict) -> str:
        aliases = self._build_multimodal_aliases(metadata, content)
        return "\n".join(
            [
                str(metadata.get("section_title") or ""),
                str(metadata.get("section_path") or ""),
                str(metadata.get("visual_tags") or ""),
                str(metadata.get("visual_evidence") or ""),
                self._visual_region_alias_text(metadata.get("visual_region")),
                aliases,
                content or "",
            ]
        )

    def _build_multimodal_aliases(self, metadata: dict, content: str) -> str:
        aliases: list[str] = []
        segment_type = str(metadata.get("segment_type") or "")
        page_number = metadata.get("page_number")
        section_title = str(metadata.get("section_title") or "")
        section_path = str(metadata.get("section_path") or "")
        merged_title = " ".join([section_title, section_path, content or ""])

        if segment_type == "page_ocr":
            aliases.extend(["扫描页", "扫描件", "影印页", "附件页", "原件页", "OCR页"])
        elif segment_type == "image_ocr":
            aliases.extend(["图片", "截图", "拍照件", "照片", "影像", "OCR图片"])
        elif segment_type == "table":
            aliases.extend(["表格", "表", "表头", "表格截图", "数据表", "清单表"])

        if page_number is not None:
            aliases.extend(
                [
                    f"第{page_number}页",
                    f"第 {page_number} 页",
                    f"page {page_number}",
                    f"p{page_number}",
                ]
            )

        if re.search(r"(盖章|公章|签章)", merged_title):
            aliases.extend(["盖章页", "签章页", "公章页"])
        if re.search(r"(签字|签署|签名)", merged_title):
            aliases.extend(["签字页", "签署页", "签名页"])
        if re.search(r"(附件|附录)", merged_title):
            aliases.extend(["附件页", "附录页"])
        if re.search(r"(图片|截图|照片|扫描)", merged_title):
            aliases.extend(["图像内容", "视觉内容"])
        visual_tags = str(metadata.get("visual_tags") or "")
        if "seal_present" in visual_tags or "stamp_present" in visual_tags:
            aliases.extend(["盖章页", "签章页", "公章页", "印章页"])
        if "signature_present" in visual_tags or "signed_page" in visual_tags:
            aliases.extend(["签字页", "签署页", "签名页"])
        if "attachment_like" in visual_tags:
            aliases.extend(["附件页", "附录页", "附件内容"])
        if "table_visual" in visual_tags:
            aliases.extend(["表格截图", "表中内容", "数据表"])
        aliases.extend(self._visual_region_alias_text(metadata.get("visual_region")).split())

        unique_aliases: list[str] = []
        for alias in aliases:
            cleaned = alias.strip()
            if cleaned and cleaned not in unique_aliases:
                unique_aliases.append(cleaned)
        return " ".join(unique_aliases)

    def _structure_match_score(self, query_variants: list[str], metadata: dict) -> float:
        heading_corpus = "\n".join(
            [
                str(metadata.get("section_title") or ""),
                str(metadata.get("section_path") or ""),
            ]
        )
        best_heading_score = 0.0
        for variant in query_variants:
            terms = self._extract_query_units(variant)
            if not terms:
                continue
            matched = sum(1 for term in terms if term in heading_corpus)
            overlap_score = matched / max(len(terms), 1)
            exact_phrase_score = 1.0 if len(variant) >= 4 and variant in heading_corpus else 0.0
            best_heading_score = max(best_heading_score, (overlap_score * 0.7) + (exact_phrase_score * 0.3))

        bonus = 0.0
        if metadata.get("table_like") and self._query_prefers_table_like(query_variants):
            bonus += 0.2
        if metadata.get("segment_type") == "list" and self._query_prefers_list_segment(query_variants):
            bonus += 0.12
        if metadata.get("segment_type") in {"image_ocr", "page_ocr"} and self._query_prefers_ocr_segment(query_variants):
            bonus += 0.18
        if metadata.get("segment_type") == "page_ocr" and self._query_mentions_page(query_variants):
            bonus += 0.08
        if metadata.get("segment_type") in {"page_ocr", "image_ocr"} and self._query_prefers_visual_evidence(query_variants):
            bonus += 0.08
        if metadata.get("segment_type") == "table" and self._query_mentions_table_capture(query_variants):
            bonus += 0.08
        if self._query_mentions_visual_region(query_variants, metadata.get("visual_region")):
            bonus += 0.12
        if self._visual_tag_match_bonus(query_variants, metadata):
            bonus += self._visual_tag_match_bonus(query_variants, metadata)
        ocr_quality = metadata.get("ocr_quality")
        if (
            ocr_quality is not None
            and metadata.get("segment_type") in {"page_ocr", "image_ocr"}
            and self._query_prefers_visual_evidence(query_variants)
        ):
            bonus += min(max(float(ocr_quality), 0.0), 1.0) * 0.08
        return min(best_heading_score + bonus, 1.0)
