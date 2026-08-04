import unittest
from unittest.mock import patch

from eval.common import ensure_eval_llm_ready, is_placeholder_api_key


class EvalCommonTests(unittest.TestCase):
    def test_is_placeholder_api_key_detects_examples(self):
        self.assertTrue(is_placeholder_api_key(""))
        self.assertTrue(is_placeholder_api_key("your-dashscope-api-key"))
        self.assertTrue(is_placeholder_api_key("your-real-key"))
        self.assertFalse(is_placeholder_api_key("sk-1234567890"))

    def test_ensure_eval_llm_ready_rejects_placeholder_key(self):
        with patch("eval.common.get_settings") as mock_get_settings:
            mock_get_settings.return_value.LLM_PROVIDER = "openai_compatible"
            mock_get_settings.return_value.LLM_API_KEY = "your-dashscope-api-key"
            with self.assertRaisesRegex(RuntimeError, "LLM_API_KEY"):
                ensure_eval_llm_ready()


if __name__ == "__main__":
    unittest.main()
