import asyncio
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import AsyncMock, patch

from eval.run_eval import run_eval_with_llm_judge, write_eval_output


class EvalLlmJudgeTests(unittest.TestCase):
    def test_optional_judge_scores_are_separate_from_deterministic_metrics(self):
        dataset = [
            {
                "name": "payment",
                "question": "预付款是多少？",
                "reference_answer": "预付款为 100 万元。",
                "expected_chunk_keywords": ["100 万元"],
                "expected_answer_keywords": ["100 万元"],
                "should_refuse": False,
            }
        ]
        rag_result = {
            "answer": "合同约定预付款为 100 万元。",
            "citations": [{"source_text": "合同约定预付款为 100 万元。"}],
            "hit_chunks": [{"content": "合同约定预付款为 100 万元。"}],
            "confidence": 0.9,
            "can_answer": True,
        }
        judge_result = '{"groundedness":0.95,"answer_relevance":0.9,"completeness":0.85,"verdict":"pass","reason":"引用支持结论"}'

        with patch("eval.run_eval.agentic_rag_service.answer", return_value=rag_result), patch(
            "eval.llm_judge.llm_service.generate", new=AsyncMock(return_value=judge_result)
        ):
            result = asyncio.run(
                run_eval_with_llm_judge(
                    dataset=dataset,
                    user_id=None,
                    top_k=5,
                    confidence_threshold=0.35,
                )
            )

        self.assertEqual(result["summary"]["citation_accuracy"], 1.0)
        self.assertTrue(result["summary"]["llm_judge"]["available"])
        self.assertEqual(result["summary"]["llm_judge"]["groundedness"], 0.95)
        self.assertEqual(result["cases"][0]["llm_judge"]["verdict"], "pass")
        self.assertTrue(result["config"]["dataset_fingerprint"])

    def test_full_report_can_be_written_to_an_explicit_path(self):
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "report.json"
            write_eval_output(path, {"summary": {"total_cases": 1}})

            self.assertTrue(path.exists())
            self.assertIn("total_cases", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
