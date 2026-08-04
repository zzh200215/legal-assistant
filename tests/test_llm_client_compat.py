import unittest

from app.core.llm_client import LLMClient, llm_client
from app.core.ollama_client import OllamaClient, ollama_client


class LLMClientCompatibilityTests(unittest.TestCase):
    def test_backward_compatibility_aliases_point_to_new_client(self):
        self.assertIs(OllamaClient, LLMClient)
        self.assertIs(ollama_client, llm_client)


if __name__ == "__main__":
    unittest.main()
