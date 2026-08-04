import asyncio
import logging
import re
import time

from app.core.config import get_settings
from app.core.llm_client import llm_client
from app.services.llm_observability_service import llm_observability_service
from app.services.prompt_service import prompt_service
from app.services.rag_runtime import resolve_runtime_config
from app.services.vector_store import build_vector_store

settings = get_settings()
logger = logging.getLogger(__name__)


class RAGService:
    def __init__(self):
        self.store = build_vector_store()
        self.client = getattr(self.store, "client", None)
        self.collection = self.store.collection

    def get_runtime_config(
        self,
        *,
        top_k: int | None = None,
        confidence_threshold: float | None = None,
        min_recall_candidates: int | None = None,
        recall_multiplier: int | None = None,
        query_variant_limit: int | None = None,
        context_neighbor_window: int | None = None,
        context_max_chunks: int | None = None,
    ) -> dict:
        return resolve_runtime_config(
            settings,
            top_k=top_k,
            confidence_threshold=confidence_threshold,
            min_recall_candidates=min_recall_candidates,
            recall_multiplier=recall_multiplier,
            query_variant_limit=query_variant_limit,
            context_neighbor_window=context_neighbor_window,
            context_max_chunks=context_max_chunks,
        )

    def index_document(self, document_id: int, chunks: list[dict], user_id: int | None = None):
        if not chunks:
            return
        ids = [chunk["embedding_id"] for chunk in chunks]
        metadatas = [
            self._compact_metadata(
                {
                    "document_id": document_id,
                    "chunk_id": chunk.get("id"),
                    "chunk_index": chunk["chunk_index"],
                    "page_number": chunk.get("page_number"),
                    "section_title": chunk.get("section_title"),
                    "embedding_id": chunk["embedding_id"],
                    "section_path": " > ".join(chunk.get("section_path") or []),
                    "segment_type": chunk.get("segment_type"),
                    "table_like": bool(chunk.get("table_like")),
                    "visual_tags": " ".join(chunk.get("visual_tags") or []),
                    "ocr_quality": chunk.get("ocr_quality"),
                    "visual_evidence": chunk.get("visual_evidence"),
                    "visual_region": chunk.get("visual_region"),
                    "user_id": user_id,
                }
            )
            for chunk in chunks
        ]
        contents = [chunk.get("index_content") or chunk["content"] for chunk in chunks]
        embeddings = asyncio.run(llm_client.embed(contents, user_id=user_id, action="embedding"))
        try:
            self.collection.delete(where={"document_id": document_id})
        except Exception:
            pass
        self.collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=contents,
            metadatas=metadatas,
        )

    @staticmethod
    def _compact_metadata(metadata: dict) -> dict:
        return {key: value for key, value in metadata.items() if value is not None}

    def _build_where(self, document_id: int | None = None, user_id: int | None = None) -> dict | None:
        clauses = []
        if document_id is not None:
            clauses.append({"document_id": document_id})
        if user_id is not None:
            clauses.append({"user_id": user_id})
        if not clauses:
            return None
        if len(clauses) == 1:
            return clauses[0]
        return {"$and": clauses}

    async def search_async(
        self,
        query: str,
        document_id: int | None = None,
        top_k: int | None = None,
        user_id: int | None = None,
        min_recall_candidates: int | None = None,
        recall_multiplier: int | None = None,
        query_variant_limit: int | None = None,
    ) -> list[dict]:
        runtime_config = self.get_runtime_config(
            top_k=top_k,
            min_recall_candidates=min_recall_candidates,
            recall_multiplier=recall_multiplier,
            query_variant_limit=query_variant_limit,
        )
        where = self._build_where(document_id=document_id, user_id=user_id)
        query_variants = self._rewrite_queries(query, limit=runtime_config["query_variant_limit"])
        candidate_limit = max(
            runtime_config["top_k"] * runtime_config["recall_multiplier"],
            runtime_config["min_recall_candidates"],
        )
        dense_candidates, keyword_candidates = await asyncio.gather(
            self._dense_multi_recall(query_variants, where=where, candidate_limit=candidate_limit, user_id=user_id),
            self._keyword_multi_recall(query_variants, where=where, candidate_limit=candidate_limit),
        )
        fused_candidates = self._rrf_fuse_candidates(
            dense_candidates=dense_candidates,
            keyword_candidates=keyword_candidates,
        )
        return self._rerank_candidates(
            query=query,
            query_variants=query_variants,
            fused_candidates=fused_candidates,
            top_k=runtime_config["top_k"],
        )

    def search(
        self,
        query: str,
        document_id: int | None = None,
        top_k: int | None = None,
        user_id: int | None = None,
        min_recall_candidates: int | None = None,
        recall_multiplier: int | None = None,
        query_variant_limit: int | None = None,
    ) -> list[dict]:
        return asyncio.run(
            self.search_async(
                query,
                document_id=document_id,
                top_k=top_k,
                user_id=user_id,
                min_recall_candidates=min_recall_candidates,
                recall_multiplier=recall_multiplier,
                query_variant_limit=query_variant_limit,
            )
        )

    async def answer_async(
        self,
        query: str,
        document_id: int | None = None,
        user_id: int | None = None,
        top_k: int | None = None,
        confidence_threshold: float | None = None,
        min_recall_candidates: int | None = None,
        recall_multiplier: int | None = None,
        query_variant_limit: int | None = None,
        context_neighbor_window: int | None = None,
        context_max_chunks: int | None = None,
    ) -> dict:
        runtime_config = self.get_runtime_config(
            top_k=top_k,
            confidence_threshold=confidence_threshold,
            min_recall_candidates=min_recall_candidates,
            recall_multiplier=recall_multiplier,
            query_variant_limit=query_variant_limit,
            context_neighbor_window=context_neighbor_window,
            context_max_chunks=context_max_chunks,
        )
        started = time.time()
        retrieval_started = time.time()
        chunks = await self.search_async(
            query,
            document_id=document_id,
            top_k=runtime_config["top_k"],
            user_id=user_id,
            min_recall_candidates=runtime_config["min_recall_candidates"],
            recall_multiplier=runtime_config["recall_multiplier"],
            query_variant_limit=runtime_config["query_variant_limit"],
        )
        retrieval_duration_ms = int((time.time() - retrieval_started) * 1000)
        return await self.answer_from_chunks_async(
            query,
            chunks=chunks,
            document_id=document_id,
            user_id=user_id,
            runtime_config=runtime_config,
            started=started,
            retrieval_duration_ms=retrieval_duration_ms,
        )

    async def answer_from_chunks_async(
        self,
        query: str,
        *,
        chunks: list[dict],
        document_id: int | None,
        user_id: int | None,
        runtime_config: dict,
        started: float | None = None,
        retrieval_duration_ms: int = 0,
        log_query: str | None = None,
    ) -> dict:
        """Generate a grounded answer from already retrieved chunks.

        Agentic retrieval loops call this method after evidence selection so the
        final answer uses the selected evidence instead of issuing a duplicate
        retrieval request.
        """
        started = started or time.time()
        if not chunks:
            result = self._build_response(
                answer="根据当前文档内容，无法确认该问题。",
                citations=[],
                hit_chunks=[],
                context_chunks=[],
                confidence=0.0,
                can_answer=False,
                refusal_reason="no_retrieval_hits",
                started=started,
                retrieval_duration_ms=retrieval_duration_ms,
                generation_duration_ms=0,
                runtime_config=runtime_config,
            )
            self._record_pipeline_log(log_query or query, document_id, user_id, runtime_config, result)
            return result

        context_chunks = await self._expand_context_chunks(
            chunks,
            document_id=document_id,
            user_id=user_id,
            neighbor_window=runtime_config["context_neighbor_window"],
            max_chunks=runtime_config["context_max_chunks"],
        )
        context = self._build_prompt_context(context_chunks)
        prompt = prompt_service.render_by_name(
            "rag_answer",
            user_id=user_id,
            question=query,
            context=context,
        )
        citations = self._build_citations(chunks)
        confidence = self._estimate_confidence(query, chunks)
        if confidence < runtime_config["confidence_threshold"]:
            result = self._build_response(
                answer="根据当前文档内容，无法确认该问题。",
                citations=citations[:2],
                hit_chunks=chunks,
                context_chunks=context_chunks,
                confidence=confidence,
                can_answer=False,
                refusal_reason="low_confidence",
                started=started,
                retrieval_duration_ms=retrieval_duration_ms,
                generation_duration_ms=0,
                runtime_config=runtime_config,
            )
            self._record_pipeline_log(log_query or query, document_id, user_id, runtime_config, result)
            return result

        metadata = prompt_service.get_template_metadata("rag_answer", user_id=user_id)
        generation_started = time.time()
        answer = await llm_client.generate(
            prompt,
            temperature=0.3,
            action="rag_answer",
            user_id=user_id,
            prompt_template=metadata.get("prompt_template"),
            prompt_version=metadata.get("prompt_version"),
        )
        generation_duration_ms = int((time.time() - generation_started) * 1000)
        normalized_answer = answer.strip()
        can_answer = not self._looks_like_refusal(normalized_answer)
        refusal_reason = None
        if not can_answer:
            normalized_answer = "根据当前文档内容，无法确认该问题。"
            confidence = min(confidence, 0.3)
            refusal_reason = "model_refusal"
        citation_grounded = self._is_answer_grounded(normalized_answer, context_chunks, can_answer=can_answer)
        if can_answer and not citation_grounded:
            normalized_answer = "根据当前文档内容，暂时无法给出可引用支持的确定答案。"
            confidence = min(confidence, 0.3)
            can_answer = False
            refusal_reason = "insufficient_citation_grounding"
        result = self._build_response(
            answer=normalized_answer,
            citations=citations,
            hit_chunks=chunks,
            context_chunks=context_chunks,
            confidence=confidence,
            can_answer=can_answer,
            refusal_reason=refusal_reason,
            started=started,
            retrieval_duration_ms=retrieval_duration_ms,
            generation_duration_ms=generation_duration_ms,
            runtime_config=runtime_config,
        )
        self._record_pipeline_log(log_query or query, document_id, user_id, runtime_config, result)
        return result

    def answer(
        self,
        query: str,
        document_id: int | None = None,
        user_id: int | None = None,
        top_k: int | None = None,
        confidence_threshold: float | None = None,
        min_recall_candidates: int | None = None,
        recall_multiplier: int | None = None,
        query_variant_limit: int | None = None,
        context_neighbor_window: int | None = None,
        context_max_chunks: int | None = None,
    ) -> dict:
        return asyncio.run(
            self.answer_async(
                query,
                document_id=document_id,
                user_id=user_id,
                top_k=top_k,
                confidence_threshold=confidence_threshold,
                min_recall_candidates=min_recall_candidates,
                recall_multiplier=recall_multiplier,
                query_variant_limit=query_variant_limit,
                context_neighbor_window=context_neighbor_window,
                context_max_chunks=context_max_chunks,
            )
        )

    @staticmethod
    def _excerpt(text: str, limit: int = 240) -> str:
        return text[:limit] + "..." if len(text) > limit else text

    def _build_citations(self, chunks: list[dict]) -> list[dict]:
        citations = []
        for chunk in chunks[:3]:
            metadata = chunk.get("metadata") or {}
            citations.append(
                {
                    "document_id": metadata.get("document_id"),
                    "chunk_id": metadata.get("chunk_id"),
                    "embedding_id": metadata.get("embedding_id"),
                    "chunk_index": metadata.get("chunk_index"),
                    "page_number": metadata.get("page_number"),
                    "section_title": metadata.get("section_title"),
                    "locator": self._build_citation_locator(metadata),
                    "document_anchor": {
                        "document_id": metadata.get("document_id"),
                        "page_number": metadata.get("page_number"),
                        "chunk_index": metadata.get("chunk_index"),
                        "section_path": metadata.get("section_path"),
                    },
                    "visual_evidence": metadata.get("visual_evidence"),
                    "visual_region": metadata.get("visual_region"),
                    "source_text": self._excerpt(chunk["content"]),
                    "retrieval_score": chunk.get("retrieval_score"),
                    "rrf_score": chunk.get("rrf_score"),
                }
            )
        return citations

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

    def _build_response(
        self,
        *,
        answer: str,
        citations: list[dict],
        hit_chunks: list[dict],
        context_chunks: list[dict],
        confidence: float,
        can_answer: bool,
        refusal_reason: str | None = None,
        started: float,
        retrieval_duration_ms: int = 0,
        generation_duration_ms: int = 0,
        runtime_config: dict | None = None,
    ) -> dict:
        latency_ms = int((time.time() - started) * 1000)
        rerank_duration_ms = max(latency_ms - retrieval_duration_ms - generation_duration_ms, 0)
        return {
            "answer": answer,
            "citations": citations,
            "confidence": round(max(0.0, min(confidence, 1.0)), 2),
            "can_answer": can_answer,
            "refusal_reason": refusal_reason,
            "hit_chunks": hit_chunks,
            "context_chunks": context_chunks,
            "latency_ms": latency_ms,
            "observability": {
                "retrieval_duration_ms": retrieval_duration_ms,
                "rerank_duration_ms": rerank_duration_ms,
                "generation_duration_ms": generation_duration_ms,
                "hit_chunk_count": len(hit_chunks),
                "context_chunk_count": len(context_chunks),
                "citation_count": len(citations),
                "result_status": "answered" if can_answer else "refused",
                "refusal_reason": refusal_reason,
            },
            "runtime_config": runtime_config or self.get_runtime_config(),
        }

    def _record_pipeline_log(
        self,
        query: str,
        document_id: int | None,
        user_id: int | None,
        runtime_config: dict,
        result: dict,
    ) -> None:
        observability = result.get("observability") or {}
        llm_observability_service.log_event(
            module_name="document",
            action="rag_pipeline",
            model_name=settings.LLM_MODEL,
            status=observability.get("result_status") or ("success" if result.get("can_answer") else "refused"),
            duration_ms=result.get("latency_ms"),
            user_id=user_id,
            request_excerpt={
                "query": query,
                "document_id": document_id,
                **runtime_config,
            },
            response_excerpt=observability,
        )

    @staticmethod
    def _build_citation_locator(metadata: dict) -> str:
        parts = []
        if metadata.get("document_id") is not None:
            parts.append(f"doc:{metadata['document_id']}")
        if metadata.get("page_number") is not None:
            parts.append(f"page:{metadata['page_number']}")
        if metadata.get("section_title"):
            parts.append(f"section:{metadata['section_title']}")
        if metadata.get("segment_type"):
            parts.append(f"type:{metadata['segment_type']}")
        if metadata.get("visual_tags"):
            parts.append(f"tags:{metadata['visual_tags']}")
        if metadata.get("visual_evidence"):
            parts.append(f"evidence:{RAGService._excerpt(str(metadata['visual_evidence']), 80)}")
        if metadata.get("visual_region"):
            parts.append(f"region:{metadata['visual_region']}")
        if metadata.get("chunk_index") is not None:
            parts.append(f"chunk:{metadata['chunk_index']}")
        return " | ".join(parts)

    def _build_prompt_context(self, chunks: list[dict]) -> str:
        blocks = []
        for index, chunk in enumerate(chunks, start=1):
            metadata = chunk.get("metadata") or {}
            parts = [f"片段 {index}"]
            if metadata.get("page_number") is not None:
                parts.append(f"page:{metadata['page_number']}")
            if metadata.get("section_title"):
                parts.append(f"section:{metadata['section_title']}")
            if metadata.get("section_path"):
                parts.append(f"path:{metadata['section_path']}")
            if metadata.get("segment_type"):
                parts.append(f"type:{metadata['segment_type']}")
            if metadata.get("visual_tags"):
                parts.append(f"tags:{metadata['visual_tags']}")
            if metadata.get("visual_region"):
                parts.append(f"region:{metadata['visual_region']}")
            if metadata.get("chunk_index") is not None:
                parts.append(f"chunk:{metadata['chunk_index']}")
            summary = self._build_visual_context_summary(metadata)
            block_content = chunk.get("content") or ""
            if summary:
                block_content = f"{summary}\n{block_content}".strip()
            if metadata.get("visual_evidence"):
                block_content = f"{block_content}\n[视觉证据]\n{metadata['visual_evidence']}".strip()
            blocks.append(f"[{' | '.join(parts)}]\n{block_content}")
        return "\n\n".join(blocks)

    async def _expand_context_chunks(
        self,
        chunks: list[dict],
        *,
        document_id: int | None,
        user_id: int | None,
        neighbor_window: int,
        max_chunks: int,
    ) -> list[dict]:
        if not chunks:
            return []
        if document_id is None or (neighbor_window <= 0 and max_chunks <= len(chunks)):
            return chunks[:max_chunks]

        where = self._build_where(document_id=document_id, user_id=user_id)
        try:
            rows = await asyncio.to_thread(
                self.collection.get,
                where=where,
                include=["documents", "metadatas"],
            )
        except Exception as exc:
            logger.warning(
                "Vector store context expansion unavailable; using retrieved chunks only (%s)",
                type(exc).__name__,
            )
            return chunks[:max_chunks]
        ids = rows.get("ids") or []
        documents = rows.get("documents") or []
        metadatas = rows.get("metadatas") or []
        by_index: dict[int, dict] = {}
        for index, chunk_id in enumerate(ids):
            metadata = metadatas[index] if index < len(metadatas) else {}
            chunk_index = metadata.get("chunk_index") if isinstance(metadata, dict) else None
            if chunk_index is None:
                continue
            by_index[int(chunk_index)] = {
                "id": chunk_id,
                "content": documents[index] if index < len(documents) else "",
                "metadata": metadata or {},
                "distance": None,
                "retrieval_score": 0.0,
                "retrieval_routes": ["context_expand"],
                "dense_score": 0.0,
                "keyword_score": 0.0,
                "structure_score": 0.0,
                "matched_variants": [],
            }

        selected: dict[str, tuple[int, int, dict]] = {}
        for rank, chunk in enumerate(chunks):
            metadata = chunk.get("metadata") or {}
            seed_index = metadata.get("chunk_index")
            seed_id = str(chunk.get("id"))
            selected.setdefault(seed_id, (rank, int(seed_index) if seed_index is not None else rank, chunk))
            if seed_index is None:
                continue
            for offset in range(-neighbor_window, neighbor_window + 1):
                neighbor = by_index.get(int(seed_index) + offset)
                if not neighbor:
                    continue
                neighbor_id = str(neighbor.get("id"))
                selected.setdefault(neighbor_id, (rank, int(seed_index) + offset, neighbor))

        ordered = sorted(selected.values(), key=lambda item: (item[0], item[1]))
        return [item[2] for item in ordered[:max_chunks]]

    def _is_answer_grounded(self, answer: str, chunks: list[dict], *, can_answer: bool) -> bool:
        if not can_answer:
            return True
        answer_terms = self._extract_query_units(answer)
        if not answer_terms:
            return True
        corpus_parts = []
        for chunk in chunks[:3]:
            metadata = chunk.get("metadata") or {}
            corpus_parts.append(chunk.get("content") or "")
            corpus_parts.append(self._build_keyword_corpus(chunk.get("content") or "", metadata))
        corpus = "\n".join(part for part in corpus_parts if part)
        matched_terms = sum(1 for term in answer_terms if term in corpus)
        return matched_terms >= min(2, len(answer_terms))

    @staticmethod
    def _build_visual_context_summary(metadata: dict) -> str:
        visual_tags = str(metadata.get("visual_tags") or "").split()
        if not visual_tags and metadata.get("ocr_quality") is None:
            return ""
        labels = {
            "ocr": "OCR识别",
            "visual": "视觉线索",
            "scanned_page": "扫描页",
            "page_visual": "页面视觉",
            "image_visual": "图片视觉",
            "table_visual": "表格视觉",
            "table_dense": "表格密集",
            "seal_present": "公章",
            "stamp_present": "印章",
            "signature_present": "签字",
            "signed_page": "签署页",
            "attachment_like": "附件页",
            "image_like": "图像内容",
            "document_copy": "扫描件/复印件",
        }
        visual_label_text = "、".join(labels.get(tag, tag.replace("_", " ")) for tag in visual_tags[:6])
        parts = []
        if visual_label_text:
            parts.append(f"视觉线索: {visual_label_text}")
        if metadata.get("ocr_quality") is not None:
            parts.append(f"OCR质量: {round(float(metadata['ocr_quality']), 2):.2f}")
        if metadata.get("visual_region"):
            region_labels = {
                "top": "页面上部",
                "middle": "页面中部",
                "bottom": "页面下部",
            }
            parts.append(f"区域: {region_labels.get(str(metadata['visual_region']), metadata['visual_region'])}")
        return f"[视觉摘要] {'；'.join(parts)}" if parts else ""

    @staticmethod
    def _looks_like_refusal(answer: str) -> bool:
        markers = ("无法确认", "不能确认", "未提及", "未找到", "信息不足", "没有相关信息")
        return any(marker in answer for marker in markers)

    @staticmethod
    def _normalize_query(query: str) -> str:
        return re.sub(r"\s+", " ", (query or "").strip())

    def _rewrite_queries(self, query: str, limit: int | None = None) -> list[str]:
        normalized = self._normalize_query(query)
        if not normalized:
            return []

        variants: list[str] = [normalized]
        simplified = normalized
        filler_patterns = [
            r"^(请问|帮我|麻烦你|麻烦|请帮我)",
            r"(一下|看看|说明一下|告诉我)$",
            r"(这份文档里|文档里|文档中|材料里|材料中)",
        ]
        for pattern in filler_patterns:
            simplified = re.sub(pattern, "", simplified)
        simplified = self._normalize_query(re.sub(r"[？?。!！,，：:]", " ", simplified))
        if simplified and simplified not in variants:
            variants.append(simplified)

        compact = re.sub(r"\s+", "", simplified)
        if compact and compact not in variants:
            variants.append(compact)

        key_terms = sorted(self._extract_query_units(simplified), key=len, reverse=True)
        if key_terms:
            focused = " ".join(key_terms[:4])
            if focused and focused not in variants:
                variants.append(focused)

        unique_variants: list[str] = []
        for variant in variants:
            cleaned = self._normalize_query(variant)
            if cleaned and cleaned not in unique_variants:
                unique_variants.append(cleaned)
        final_limit = max(1, int(limit if limit is not None else settings.RAG_QUERY_VARIANT_LIMIT))
        return unique_variants[:final_limit]

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

    async def _keyword_multi_recall(
        self,
        query_variants: list[str],
        *,
        where: dict | None,
        candidate_limit: int,
    ) -> list[dict]:
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

    @staticmethod
    def _distance_score(distance: float | None) -> float:
        if distance is None:
            return 0.6
        return max(0.0, min(1.0, 1 - (distance / 2.0)))

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

    @staticmethod
    def _visual_region_alias_text(region: str | None) -> str:
        region_key = str(region or "").strip().lower()
        if not region_key:
            return ""
        mapping = {
            "top": ["top", "upper", "页面上部", "上部", "上方", "顶部"],
            "middle": ["middle", "center", "centre", "页面中部", "中部", "中间", "居中"],
            "bottom": ["bottom", "lower", "页面下部", "下部", "下方", "底部"],
        }
        return " ".join(mapping.get(region_key, [region_key]))

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

    @staticmethod
    def _visual_tag_match_bonus(query_variants: list[str], metadata: dict) -> float:
        query_text = " ".join(query_variants)
        visual_tags = set(str(metadata.get("visual_tags") or "").split())
        bonus = 0.0
        if visual_tags.intersection({"seal_present", "stamp_present"}) and re.search(r"(盖章|签章|公章|印章)", query_text):
            bonus += 0.12
        if visual_tags.intersection({"signature_present", "signed_page"}) and re.search(r"(签字|签署|签名)", query_text):
            bonus += 0.12
        if "attachment_like" in visual_tags and re.search(r"(附件|附录|附页)", query_text):
            bonus += 0.08
        if "table_visual" in visual_tags and re.search(r"(表格|表中|表里|表头|数据表)", query_text):
            bonus += 0.08
        return min(bonus, 0.2)

    @staticmethod
    def _query_prefers_table_like(query_variants: list[str]) -> bool:
        query_text = " ".join(query_variants)
        if re.search(r"\d{4}[-/年]\d{1,2}", query_text):
            return True
        return bool(
            re.search(
                r"(金额|付款|支付|费用|报价|价格|税率|比例|数量|统计|汇总|日期|时间|期限|截止|节点|发票|对账)",
                query_text,
            )
        )

    @staticmethod
    def _query_prefers_list_segment(query_variants: list[str]) -> bool:
        query_text = " ".join(query_variants)
        return bool(re.search(r"(步骤|流程|清单|列表|要求|材料|职责|安排|要点|范围|条件)", query_text))

    @staticmethod
    def _query_prefers_ocr_segment(query_variants: list[str]) -> bool:
        query_text = " ".join(query_variants)
        return bool(re.search(r"(扫描|扫描件|影印|图片|截图|拍照|照片|页码|第.?页|附图|原件)", query_text))

    @staticmethod
    def _query_mentions_page(query_variants: list[str]) -> bool:
        query_text = " ".join(query_variants)
        return bool(re.search(r"(第\s*\d+\s*页|页码|\d+\s*页)", query_text))

    @staticmethod
    def _query_prefers_visual_evidence(query_variants: list[str]) -> bool:
        query_text = " ".join(query_variants)
        return bool(re.search(r"(盖章|签章|公章|签字|签名|截图|照片|图片里|扫描件里|影印件)", query_text))

    @staticmethod
    def _query_mentions_table_capture(query_variants: list[str]) -> bool:
        query_text = " ".join(query_variants)
        return bool(re.search(r"(表格|表头|截图表格|表中|表里|列表图|数据表)", query_text))

    @classmethod
    def _query_mentions_visual_region(cls, query_variants: list[str], region: str | None) -> bool:
        region_aliases = cls._visual_region_alias_text(region).split()
        if not region_aliases:
            return False
        query_text = " ".join(query_variants)
        return any(alias and alias in query_text for alias in region_aliases)

    @staticmethod
    def _extract_query_units(query: str) -> set[str]:
        terms = set(re.findall(r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", query))
        compact = re.sub(r"\s+", "", query)
        if re.search(r"[\u4e00-\u9fff]", compact):
            for index in range(len(compact) - 1):
                gram = compact[index : index + 2]
                if re.search(r"[\u4e00-\u9fff]{2}", gram):
                    terms.add(gram)
        return {term for term in terms if len(term.strip()) >= 2}

    def _keyword_overlap_score(self, query: str, chunks: list[dict]) -> float:
        terms = self._extract_query_units(query)
        if not terms:
            return 0.5
        matched = 0
        corpus = "\n".join(chunk["content"] for chunk in chunks[:3])
        for term in terms:
            if term in corpus:
                matched += 1
        return matched / max(len(terms), 1)

    def _estimate_confidence(self, query: str, chunks: list[dict]) -> float:
        keyword_score = self._keyword_overlap_score(query, chunks)
        distance_scores = [self._distance_score(chunk.get("distance")) for chunk in chunks[:3]]
        distance_score = sum(distance_scores) / len(distance_scores) if distance_scores else 0.0
        return (keyword_score * 0.55) + (distance_score * 0.45)


rag_service = RAGService()
