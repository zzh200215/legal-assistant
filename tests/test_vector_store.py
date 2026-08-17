import unittest
from unittest.mock import patch

from app.services.rag import vector_store


class VectorStoreSelectionTests(unittest.TestCase):
    def test_build_vector_store_defaults_to_chroma(self):
        with patch("app.services.rag.vector_store.settings.VECTOR_STORE_PROVIDER", "chroma"), patch(
            "app.services.rag.vector_store.ChromaVectorStore",
            return_value="chroma-store",
        ) as mock_chroma:
            store = vector_store.build_vector_store()

        self.assertEqual(store, "chroma-store")
        mock_chroma.assert_called_once()

    def test_build_vector_store_supports_qdrant_provider(self):
        with patch("app.services.rag.vector_store.settings.VECTOR_STORE_PROVIDER", "qdrant"), patch(
            "app.services.rag.vector_store.QdrantVectorStore",
            return_value="qdrant-store",
        ) as mock_qdrant:
            store = vector_store.build_vector_store()

        self.assertEqual(store, "qdrant-store")
        mock_qdrant.assert_called_once()


if __name__ == "__main__":
    unittest.main()
