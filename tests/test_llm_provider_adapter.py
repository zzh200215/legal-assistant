import unittest

from app.core.llm_provider_adapter import provider_adapter


class LLMProviderAdapterTests(unittest.TestCase):
    def test_openai_compatible_protocol(self):
        adapter = provider_adapter("openai_compatible")

        self.assertEqual(adapter.chat_url("https://example.test/v1"), "https://example.test/v1/chat/completions")
        self.assertEqual(adapter.headers("key-1")["Authorization"], "Bearer key-1")
        self.assertEqual(
            adapter.extract_chat_content({"choices": [{"message": {"content": "answer"}}]}),
            "answer",
        )

    def test_ollama_protocol(self):
        adapter = provider_adapter("ollama")

        self.assertEqual(adapter.chat_url("http://localhost:11434"), "http://localhost:11434/api/chat")
        self.assertNotIn("Authorization", adapter.headers("unused"))
        self.assertEqual(adapter.extract_embeddings({"embeddings": [0.1, 0.2]}), [[0.1, 0.2]])
        self.assertEqual(adapter.extract_embeddings({"embeddings": []}), [])


if __name__ == "__main__":
    unittest.main()
