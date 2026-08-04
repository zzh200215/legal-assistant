from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.core.config import get_settings

settings = get_settings()

try:
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qdrant_models

    QDRANT_AVAILABLE = True
except Exception:
    QdrantClient = None
    qdrant_models = None
    QDRANT_AVAILABLE = False


class VectorStoreCollection:
    def add(self, *, ids: list[str], embeddings: list[list[float]], documents: list[str], metadatas: list[dict]) -> None:
        raise NotImplementedError

    def delete(self, *, where: dict | None = None) -> None:
        raise NotImplementedError

    def get(self, *, where: dict | None = None, include: list[str] | None = None) -> dict[str, Any]:
        raise NotImplementedError

    def query(self, *, query_embeddings: list[list[float]], n_results: int, where: dict | None = None) -> dict[str, Any]:
        raise NotImplementedError


@dataclass
class ChromaVectorStoreCollection(VectorStoreCollection):
    collection: Any

    def add(self, *, ids: list[str], embeddings: list[list[float]], documents: list[str], metadatas: list[dict]) -> None:
        self.collection.add(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)

    def delete(self, *, where: dict | None = None) -> None:
        self.collection.delete(where=where)

    def get(self, *, where: dict | None = None, include: list[str] | None = None) -> dict[str, Any]:
        return self.collection.get(where=where, include=include)

    def query(self, *, query_embeddings: list[list[float]], n_results: int, where: dict | None = None) -> dict[str, Any]:
        return self.collection.query(query_embeddings=query_embeddings, n_results=n_results, where=where)


class QdrantVectorStoreCollection(VectorStoreCollection):
    def __init__(self, client: Any, collection_name: str) -> None:
        self.client = client
        self.collection_name = collection_name
        self._vector_size: int | None = None

    def add(self, *, ids: list[str], embeddings: list[list[float]], documents: list[str], metadatas: list[dict]) -> None:
        if not embeddings:
            return
        self._ensure_collection(len(embeddings[0]))
        points = []
        for point_id, embedding, document, metadata in zip(ids, embeddings, documents, metadatas):
            payload = dict(metadata or {})
            payload["content"] = document
            points.append(
                qdrant_models.PointStruct(
                    id=str(point_id),
                    vector=embedding,
                    payload=payload,
                )
            )
        self.client.upsert(collection_name=self.collection_name, points=points, wait=True)

    def delete(self, *, where: dict | None = None) -> None:
        self._ensure_existing_collection()
        if not where:
            self.client.delete_collection(self.collection_name)
            self._vector_size = None
            return
        q_filter = self._build_filter(where)
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=qdrant_models.FilterSelector(filter=q_filter),
            wait=True,
        )

    def get(self, *, where: dict | None = None, include: list[str] | None = None) -> dict[str, Any]:
        if not self._collection_exists():
            return {"ids": [], "documents": [], "metadatas": []}
        _ = include
        q_filter = self._build_filter(where) if where else None
        all_points = []
        next_offset = None
        while True:
            points, next_offset = self.client.scroll(
                collection_name=self.collection_name,
                scroll_filter=q_filter,
                limit=256,
                offset=next_offset,
                with_payload=True,
                with_vectors=False,
            )
            all_points.extend(points)
            if next_offset is None:
                break
        ids = []
        documents = []
        metadatas = []
        for point in all_points:
            payload = dict(point.payload or {})
            ids.append(str(point.id))
            documents.append(str(payload.pop("content", "")))
            metadatas.append(payload)
        return {"ids": ids, "documents": documents, "metadatas": metadatas}

    def query(self, *, query_embeddings: list[list[float]], n_results: int, where: dict | None = None) -> dict[str, Any]:
        if not self._collection_exists():
            return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}
        q_filter = self._build_filter(where) if where else None
        ids = []
        documents = []
        metadatas = []
        distances = []
        for embedding in query_embeddings:
            hits = self.client.search(
                collection_name=self.collection_name,
                query_vector=embedding,
                limit=n_results,
                query_filter=q_filter,
                with_payload=True,
                with_vectors=False,
            )
            row_ids = []
            row_docs = []
            row_meta = []
            row_distances = []
            for hit in hits:
                payload = dict(hit.payload or {})
                row_ids.append(str(hit.id))
                row_docs.append(str(payload.pop("content", "")))
                row_meta.append(payload)
                score = float(hit.score or 0.0)
                row_distances.append(max(0.0, 1.0 - score))
            ids.append(row_ids)
            documents.append(row_docs)
            metadatas.append(row_meta)
            distances.append(row_distances)
        return {
            "ids": ids,
            "documents": documents,
            "metadatas": metadatas,
            "distances": distances,
        }

    def _ensure_collection(self, vector_size: int) -> None:
        if self._collection_exists():
            if self._vector_size is None:
                self._vector_size = vector_size
            return
        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=qdrant_models.VectorParams(
                size=vector_size,
                distance=qdrant_models.Distance.COSINE,
            ),
        )
        self._vector_size = vector_size

    def _ensure_existing_collection(self) -> None:
        if not self._collection_exists():
            raise ValueError(f"Qdrant collection not found: {self.collection_name}")

    def _collection_exists(self) -> bool:
        try:
            return self.client.collection_exists(self.collection_name)
        except Exception:
            try:
                self.client.get_collection(self.collection_name)
                return True
            except Exception:
                return False

    def _build_filter(self, where: dict) -> Any:
        clauses = where.get("$and") if isinstance(where, dict) else None
        if isinstance(clauses, list):
            must = []
            for clause in clauses:
                must.extend(self._build_filter(clause).must or [])
            return qdrant_models.Filter(must=must)

        must = []
        must_not = []
        for key, value in (where or {}).items():
            if isinstance(value, dict):
                op, operand = next(iter(value.items()))
                if op == "$ne":
                    must_not.append(
                        qdrant_models.FieldCondition(
                            key=key,
                            match=qdrant_models.MatchValue(value=operand),
                        )
                    )
                    continue
            must.append(
                qdrant_models.FieldCondition(
                    key=key,
                    match=qdrant_models.MatchValue(value=value),
                )
            )
        return qdrant_models.Filter(must=must, must_not=must_not or None)


class ChromaVectorStore:
    def __init__(self, collection_name: str | None = None) -> None:
        self.client = chromadb.PersistentClient(
            path=settings.CHROMA_PERSIST_DIR,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self.collection = ChromaVectorStoreCollection(
            self.client.get_or_create_collection(collection_name or settings.VECTOR_STORE_COLLECTION_NAME)
        )


class QdrantVectorStore:
    def __init__(self, collection_name: str | None = None) -> None:
        if not QDRANT_AVAILABLE:
            raise RuntimeError("qdrant-client is not installed")
        if settings.QDRANT_URL:
            self.client = QdrantClient(
                url=settings.QDRANT_URL,
                api_key=settings.QDRANT_API_KEY or None,
            )
        else:
            self.client = QdrantClient(path=settings.QDRANT_PERSIST_DIR)
        self.collection = QdrantVectorStoreCollection(
            self.client,
            collection_name or settings.VECTOR_STORE_COLLECTION_NAME,
        )


def build_vector_store(collection_name: str | None = None):
    provider = (settings.VECTOR_STORE_PROVIDER or "chroma").strip().lower()
    if provider == "qdrant":
        return QdrantVectorStore(collection_name)
    return ChromaVectorStore(collection_name)
