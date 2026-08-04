import asyncio
import unittest
from unittest.mock import patch

from app.services.legal_service import NO_VALID_SOURCE, REFUSAL_ADVICE
from eval.run_generation_eval import build_db, eval_consultation_case, run_eval


def _base_gold(**overrides):
    gold = {
        "expected_category": "other",
        "citation_must_match_patterns": [],
        "must_not_fabricate_winrate": True,
        "must_have_missing_facts": False,
        "risk_level_min": "low",
    }
    gold.update(overrides)
    return gold


def _case(case_id, gold):
    return {"id": case_id, "question": f"case-{case_id}", "gold": gold}


class ConsultationRefusalEvalTests(unittest.TestCase):
    def test_refusal_expected_and_advice_refused_passes(self):
        case = _case("r1", _base_gold(refusal_expected=True, risk_level_min="high"))
        result = ("other", [], [], [], REFUSAL_ADVICE, "high", "needs_lawyer_review")
        metrics = eval_consultation_case(case, result)
        self.assertTrue(metrics["refusal_ok"])
        self.assertTrue(metrics["pass"])

    def test_refusal_expected_but_advice_gives_instructions_fails(self):
        case = _case("r2", _base_gold(refusal_expected=True, risk_level_min="high"))
        result = ("other", [], [], [], "你可以这样操作：先……再……", "high", "needs_lawyer_review")
        metrics = eval_consultation_case(case, result)
        self.assertFalse(metrics["refusal_ok"])
        self.assertFalse(metrics["pass"])

    def test_inactive_source_advice_contains_no_valid_source_passes(self):
        case = _case("i1", _base_gold(inactive_sources_only=True))
        result = ("other", [], [], [], f"{NO_VALID_SOURCE} 仅供参考", "medium", "needs_lawyer_review")
        metrics = eval_consultation_case(case, result)
        self.assertTrue(metrics["no_valid_source_ok"])
        self.assertTrue(metrics["pass"])

    def test_inactive_source_but_advice_omits_warning_fails(self):
        case = _case("i2", _base_gold(inactive_sources_only=True))
        result = ("other", [], [], [], "这是一个确定性的结论。", "medium", "needs_lawyer_review")
        metrics = eval_consultation_case(case, result)
        self.assertFalse(metrics["no_valid_source_ok"])
        self.assertFalse(metrics["pass"])

    def test_plain_case_evaluation_unchanged(self):
        case = _case(
            "p1",
            _base_gold(
                expected_category="labor_dispute",
                citation_must_match_patterns=["劳动"],
                must_have_missing_facts=True,
                risk_level_min="medium",
            ),
        )
        result = (
            "labor_dispute", ["事实"], ["缺失"], [{"citation": "劳动合同法第40条"}],
            "按法条处理", "medium", "needs_lawyer_review",
        )
        metrics = eval_consultation_case(case, result)
        self.assertTrue(metrics["pass"])


class EvalReportLayerStatsTests(unittest.TestCase):
    def test_report_contains_refusal_and_inactive_layer_stats(self):
        dataset = {
            "version": "2.0",
            "contract_review_cases": [],
            "draft_generation_cases": [],
            "consultation_cases": [
                _case("refuse-case", _base_gold(refusal_expected=True, risk_level_min="high")),
                _case("inactive-case", _base_gold(inactive_sources_only=True)),
            ],
        }

        async def fake_consultation(question, sources, user_id=None):
            if "refuse" in question:
                return ("other", [], [], [], REFUSAL_ADVICE, "high", "needs_lawyer_review")
            return ("other", [], [], [], f"{NO_VALID_SOURCE} 仅供参考", "medium", "needs_lawyer_review")

        db = build_db()
        with patch("eval.run_generation_eval.consultation_payload", side_effect=fake_consultation):
            report = asyncio.run(run_eval(dataset, db))

        self.assertEqual(report["consultation"]["refusal"]["total_cases"], 1)
        self.assertEqual(report["consultation"]["refusal"]["pass_rate"], 1.0)
        self.assertEqual(report["consultation"]["inactive_source"]["total_cases"], 1)
        self.assertEqual(report["consultation"]["inactive_source"]["pass_rate"], 1.0)
        self.assertEqual(report["consultation"]["badcases"], [])


if __name__ == "__main__":
    unittest.main()
