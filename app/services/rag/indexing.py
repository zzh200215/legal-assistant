"""文档索引与元数据簇：向量写入、增量 upsert、元数据刷新。

这些方法原本是 ``RAGService`` 上的文档索引方法，以 mixin 形式抽离以削减上帝类规模。
它们依赖宿主实例状态（``self.collection``）、检索失效信号（``RetrievalMixin._invalidate_bm25``）
与元数据哈希委托（``self._compact_metadata``/``self._content_hash``/``self._metadata_hash``），
因此仍通过 ``self.`` 访问，行为不变。
"""

import logging

from app.core.async_utils import run_async
from app.core.config import get_settings
from app.core.llm_client import llm_client
from app.services.rag.rag_cache import rag_embedding_cache

settings = get_settings()
logger = logging.getLogger(__name__)


class IndexingMixin:
    def index_document(self, document_id: int, chunks: list[dict], user_id: int | None = None,
                       *, knowledge_base_id: int | None = None, document_status: str | None = None):
        # 文档内容清空（删除/重解析为空）：移除该文档全部旧向量，避免旧内容继续参与检索
        if not chunks:
            self.collection.delete(where={"document_id": document_id})
            self._invalidate_bm25()
            return
        ids = [chunk["embedding_id"] for chunk in chunks]
        metadatas = [
            self._build_index_metadata(
                document_id,
                chunk,
                user_id=user_id,
                knowledge_base_id=knowledge_base_id,
                document_status=document_status,
            )
            for chunk in chunks
        ]
        contents = [chunk.get("index_content") or chunk["content"] for chunk in chunks]

        # 索引增量：内容 / 嵌入模型 / 元数据（含 knowledge_base_id、document_status）任一变化则
        # upsert（同 ID 覆盖旧向量），已移除 chunk 删除；未变 chunk 向量原样保留。
        old_by_id: dict[str, str] = {}
        old_meta_by_id: dict[str, dict] = {}
        try:
            old_rows = self.collection.get(
                where={"document_id": document_id}, include=["documents", "metadatas"]
            )
            old_ids = old_rows.get("ids") or []
            old_docs = old_rows.get("documents") or []
            old_metas = old_rows.get("metadatas") or []
            old_by_id = dict(zip(old_ids, old_docs))
            old_meta_by_id = dict(zip(old_ids, old_metas))
        except Exception:
            pass

        def needs_rebuild(index: int, chunk_id: str) -> bool:
            if old_by_id.get(chunk_id) != contents[index]:
                return True
            old_meta = old_meta_by_id.get(chunk_id) or {}
            # 老数据无 embedding_model 视为沿用当前模型（保持增量跳过）；一旦记录了旧模型则必须比对
            old_model = str(old_meta.get("embedding_model") or "")
            if old_model and old_model != settings.EMBEDDING_MODEL:
                return True
            old_hash = old_meta.get("metadata_hash")
            if old_hash is not None and old_hash != metadatas[index].get("metadata_hash"):
                return True
            return False

        to_update_idx = [i for i, cid in enumerate(ids) if needs_rebuild(i, cid)]
        new_id_set = set(ids)
        to_delete_ids = [cid for cid in old_by_id if cid not in new_id_set]
        if to_delete_ids:
            try:
                self.collection.delete(ids=to_delete_ids)
            except Exception as exc:
                logger.warning("Failed to delete removed chunks %s (%s)", to_delete_ids, type(exc).__name__)

        if not to_update_idx:
            if to_delete_ids:
                self._invalidate_bm25()
            return

        update_ids = [ids[i] for i in to_update_idx]
        update_contents = [contents[i] for i in to_update_idx]
        update_metadatas = [metadatas[i] for i in to_update_idx]
        if settings.RAG_EMBED_CACHE_ENABLED:
            # 内容寻址：未变 chunk 命中缓存不再重算嵌入
            embeddings = run_async(
                rag_embedding_cache.get_or_compute_batch(
                    update_contents,
                    lambda ms: llm_client.embed(ms, user_id=user_id, action="embedding"),
                )
            )
        else:
            embeddings = run_async(llm_client.embed(update_contents, user_id=user_id, action="embedding"))

        self.collection.upsert(
            ids=update_ids,
            embeddings=embeddings,
            documents=update_contents,
            metadatas=update_metadatas,
        )
        self._invalidate_bm25()

    def _build_index_metadata(self, document_id: int, chunk: dict, *, user_id: int | None,
                              knowledge_base_id: int | None, document_status: str | None) -> dict:
        metadata = self._compact_metadata(
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
                "knowledge_base_id": knowledge_base_id,
                "document_status": document_status,
            }
        )
        metadata["content_hash"] = self._content_hash(chunk.get("index_content") or chunk["content"])
        metadata["metadata_hash"] = self._metadata_hash(metadata)
        metadata["embedding_model"] = settings.EMBEDDING_MODEL
        return metadata

    def refresh_document_metadata(self, document_id: int, *, user_id: int | None = None,
                                  knowledge_base_id: int | None = None,
                                  document_status: str | None = None) -> None:
        """权限/范围元数据变化时刷新 chunk 元数据，不重算嵌入（复用已有向量）。"""
        rows = self.collection.get(
            where={"document_id": document_id},
            include=["documents", "metadatas", "embeddings"],
        )
        ids = rows.get("ids") or []
        if not ids:
            return
        documents = rows.get("documents") or []
        embeddings = rows.get("embeddings") or []
        metadatas = rows.get("metadatas") or []
        new_metadatas = []
        for index, chunk_id in enumerate(ids):
            metadata = dict(metadatas[index] or {})
            if user_id is not None:
                metadata["user_id"] = user_id
            if knowledge_base_id is not None:
                metadata["knowledge_base_id"] = knowledge_base_id
            if document_status is not None:
                metadata["document_status"] = document_status
            metadata["metadata_hash"] = self._metadata_hash(metadata)
            new_metadatas.append(metadata)
        self.collection.upsert(ids=ids, embeddings=embeddings, documents=documents, metadatas=new_metadatas)
        self._invalidate_bm25()
