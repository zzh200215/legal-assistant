"""向量库适配器契约测试。

覆盖 upsert / delete(ids, where) / get(embeddings) / query(where) 在
Chroma 与 Qdrant 两种适配器上语义一致（含 $in / $ne 过滤）。
Chroma 用真实本地 PersistentClient；Qdrant 用记录型假 client（q=client 未安装
也能跑），验证适配器正确翻译契约。
"""
import types
import unittest
from unittest.mock import patch

from app.services.rag.vector_store import (
    ChromaVectorStoreCollection,
    QdrantVectorStoreCollection,
)


class ChromaVectorStoreContractTests(unittest.TestCase):
    def setUp(self):
        import uuid

        import chromadb

        # EphemeralClient 底层共享同一内存 sqlite，须用唯一集合名隔离用例
        client = chromadb.EphemeralClient()
        self.collection = ChromaVectorStoreCollection(
            client.get_or_create_collection(f"contract_{uuid.uuid4().hex}")
        )

    def _upsert(self, pairs):
        self.collection.upsert(
            ids=[cid for cid, _ in pairs],
            embeddings=[[float(index), 0.1] for index, (_, _) in enumerate(pairs)],
            documents=[content for _, content in pairs],
            metadatas=[{"document_id": 1} for _ in pairs],
        )

    def test_upsert_same_id_replaces_old_content(self):
        """核心契约：同 ID 再 upsert 必须替换旧内容，而非保留旧向量。"""
        self._upsert([("doc1_chunk0", "旧内容")])
        self._upsert([("doc1_chunk0", "新内容")])
        rows = self.collection.get(where={"document_id": 1}, include=["documents"])
        self.assertEqual(rows["documents"], ["新内容"])

    def test_upsert_preserves_unrelated_ids(self):
        self._upsert([("doc1_chunk0", "a"), ("doc1_chunk1", "b")])
        self._upsert([("doc1_chunk0", "a2")])
        rows = self.collection.get(where={"document_id": 1}, include=["documents"])
        self.assertEqual(set(rows["documents"]), {"a2", "b"})

    def test_delete_by_ids_removes_only_matching(self):
        self._upsert([("doc1_chunk0", "a"), ("doc1_chunk1", "b")])
        self.collection.delete(ids=["doc1_chunk0"])
        rows = self.collection.get(where={"document_id": 1}, include=["documents"])
        self.assertEqual(rows["documents"], ["b"])

    def test_delete_by_where_removes_matching(self):
        self.collection.upsert(
            ids=["a", "b"],
            embeddings=[[0.1], [0.2]],
            documents=["a", "b"],
            metadatas=[{"document_id": 1}, {"document_id": 2}],
        )
        self.collection.delete(where={"document_id": 1})
        rows = self.collection.get(include=["documents"])
        self.assertEqual(rows["documents"], ["b"])

    def test_get_with_embeddings_include_returns_embeddings(self):
        self._upsert([("doc1_chunk0", "a")])
        rows = self.collection.get(where={"document_id": 1}, include=["embeddings"])
        self.assertEqual(len(rows["embeddings"]), 1)
        self.assertIsNotNone(rows["embeddings"][0])

    def test_query_with_in_filter(self):
        self.collection.upsert(
            ids=["a", "b", "c"],
            embeddings=[[0.1, 0.0], [0.2, 0.0], [0.3, 0.0]],
            documents=["a", "b", "c"],
            metadatas=[{"document_id": 1}, {"document_id": 2}, {"document_id": 3}],
        )
        result = self.collection.query(
            query_embeddings=[[0.15, 0.0]],
            n_results=10,
            where={"document_id": {"$in": [1, 2]}},
        )
        ids = result["ids"][0]
        self.assertIn("a", ids)
        self.assertIn("b", ids)
        self.assertNotIn("c", ids)

    def test_query_with_ne_filter(self):
        self.collection.upsert(
            ids=["a", "b"],
            embeddings=[[0.1], [0.2]],
            documents=["a", "b"],
            metadatas=[{"document_id": 1}, {"document_id": 2}],
        )
        result = self.collection.query(
            query_embeddings=[[0.15]],
            n_results=10,
            where={"document_id": {"$ne": 2}},
        )
        self.assertEqual(result["ids"][0], ["a"])


class FakeModels:
    @staticmethod
    def PointStruct(*, id, vector, payload):
        return {"id": id, "vector": vector, "payload": payload}

    @staticmethod
    def FilterSelector(*, filter):
        return {"filter": filter}

    @staticmethod
    def PointIdsList(*, points):
        return {"points": points}

    @staticmethod
    def Filter(*, must=None, must_not=None):
        return {"must": must or [], "must_not": must_not}

    @staticmethod
    def FieldCondition(*, key, match):
        return {"key": key, "match": match}

    @staticmethod
    def MatchValue(*, value):
        return {"value": value}

    @staticmethod
    def MatchAny(*, any):
        return {"any": any}

    @staticmethod
    def VectorParams(*, size, distance):
        return {"size": size, "distance": distance}


class Distance:
    COSINE = "cosine"


FakeModels.Distance = Distance


class FakeQdrantClient:
    def __init__(self):
        self.upsert_calls = []
        self.delete_calls = []
        self.scroll_calls = []
        self.search_calls = []
        self.delete_collection_count = 0
        self.collection_present = True
        self.scroll_points = []
        self.search_hits = []

    def collection_exists(self, collection_name):
        return self.collection_present

    def create_collection(self, collection_name, vectors_config):
        self.collection_present = True

    def upsert(self, collection_name, points, wait=True):
        self.upsert_calls.append(points)

    def delete(self, collection_name, points_selector, wait=True):
        self.delete_calls.append(points_selector)

    def delete_collection(self, collection_name):
        self.delete_collection_count += 1
        self.collection_present = False

    def scroll(self, collection_name, scroll_filter=None, limit=256, offset=None,
               with_payload=True, with_vectors=False):
        self.scroll_calls.append(
            {"filter": scroll_filter, "with_vectors": with_vectors, "offset": offset}
        )
        return self.scroll_points, None

    def search(self, collection_name, query_vector, limit=None, query_filter=None,
               with_payload=True, with_vectors=False):
        self.search_calls.append({"vector": query_vector, "filter": query_filter, "limit": limit})
        return self.search_hits


class QdrantVectorStoreContractTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeQdrantClient()
        self.patchers = [
            patch("app.services.rag.vector_store.QDRANT_AVAILABLE", True),
            patch("app.services.rag.vector_store.qdrant_models", FakeModels),
        ]
        for p in self.patchers:
            p.start()
        self.addCleanup(self.stop_patchers)
        self.collection = QdrantVectorStoreCollection(self.client, "contract_test")

    def stop_patchers(self):
        for p in self.patchers:
            p.stop()

    def test_upsert_builds_points_with_content_payload(self):
        self.collection.upsert(
            ids=["doc1_chunk0"],
            embeddings=[[0.1, 0.2]],
            documents=["内容"],
            metadatas=[{"document_id": 1}],
        )
        point = self.client.upsert_calls[0][0]
        self.assertEqual(point["id"], "doc1_chunk0")
        self.assertEqual(point["vector"], [0.1, 0.2])
        self.assertEqual(point["payload"]["content"], "内容")
        self.assertEqual(point["payload"]["document_id"], 1)

    def test_delete_by_ids_uses_point_ids_selector(self):
        self.collection.delete(ids=["doc1_chunk0", "doc1_chunk1"])
        self.assertEqual(self.client.delete_calls, [{"points": ["doc1_chunk0", "doc1_chunk1"]}])

    def test_delete_by_where_in_filter(self):
        self.collection.delete(where={"document_id": {"$in": [1, 2]}})
        selector = self.client.delete_calls[0]
        self.assertEqual(selector["filter"]["must"][0]["match"], {"any": [1, 2]})

    def test_delete_by_where_ne_filter(self):
        self.collection.delete(where={"document_id": {"$ne": 3}})
        selector = self.client.delete_calls[0]
        self.assertEqual(selector["filter"]["must_not"][0]["match"], {"value": 3})

    def test_delete_without_selectors_drops_collection(self):
        self.collection.delete()
        self.assertEqual(self.client.delete_collection_count, 1)

    def test_get_with_embeddings_include_scrolls_with_vectors(self):
        point = types.SimpleNamespace(id="doc1_chunk0", payload={"content": "内容", "document_id": 1}, vector=[0.1, 0.2])
        self.client.scroll_points = [point]
        rows = self.collection.get(where={"document_id": 1}, include=["embeddings"])
        self.assertEqual(self.client.scroll_calls[0]["with_vectors"], True)
        self.assertEqual(rows["embeddings"], [[0.1, 0.2]])
        self.assertEqual(rows["documents"], ["内容"])
        self.assertNotIn("content", rows["metadatas"][0])

    def test_query_returns_expected_shape(self):
        hit = types.SimpleNamespace(
            id="doc1_chunk0",
            payload={"content": "内容", "document_id": 1},
            score=0.8,
        )
        self.client.search_hits = [hit]
        result = self.collection.query(query_embeddings=[[0.1]], n_results=5, where={"document_id": 1})
        self.assertEqual(result["ids"], [["doc1_chunk0"]])
        self.assertEqual(result["documents"], [["内容"]])
        self.assertEqual(result["metadatas"], [[{"document_id": 1}]])
        self.assertAlmostEqual(result["distances"][0][0], 0.2)


if __name__ == "__main__":
    unittest.main()
