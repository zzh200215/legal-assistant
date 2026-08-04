"""#92/缺失条款识别容错判定（_is_missing_flag + eval_contract_review_case）回归测试"""
import unittest

from eval.run_generation_eval import DISCLAIMER, _is_missing_flag, eval_contract_review_case


class MissingFlagTests(unittest.TestCase):
    def test_status_needs_facts(self):
        self.assertTrue(_is_missing_flag({"clause_type": "breach", "status": "needs_facts"}))

    def test_status_missing_normalized(self):
        self.assertTrue(_is_missing_flag({"clause_type": "breach", "status": "missing"}))

    def test_description_signal_without_status(self):
        self.assertTrue(_is_missing_flag({"clause_type": "breach", "status": "open", "description": "合同未约定违约责任条款"}))

    def test_suggestion_signal(self):
        self.assertTrue(_is_missing_flag({"clause_type": "ip", "status": "open", "description": "存在风险", "suggestion": "建议补充知识产权条款（缺失）"}))

    def test_normal_open_not_flag(self):
        self.assertFalse(_is_missing_flag({"clause_type": "payment", "status": "open", "description": "付款节点需确认"}))


class ContractCaseFuzzyDetectionTests(unittest.TestCase):
    def test_cr006_passes_with_soft_signal(self):
        """cr_006 场景：模型未标 needs_facts 但描述含缺失信号 → 容错判定通过"""
        case = {
            "id": "cr_006",
            "category": "risky",
            "gold": {
                "expected_present_clauses": ["compensation", "confidentiality", "delivery", "dispute_resolution", "ip", "payment", "termination"],
                "expected_absent_clauses": ["breach"],
                "min_high_risk_count": 3,
            },
        }
        risks = [
            {"clause_type": "payment", "risk_level": "medium", "status": "open", "description": "付款安排", "suggestion": ""},
            {"clause_type": "delivery", "risk_level": "medium", "status": "open", "description": "交付安排", "suggestion": ""},
            {"clause_type": "compensation", "risk_level": "high", "status": "open", "description": "赔偿条款", "suggestion": ""},
            {"clause_type": "confidentiality", "risk_level": "medium", "status": "open", "description": "保密条款", "suggestion": ""},
            {"clause_type": "ip", "risk_level": "high", "status": "open", "description": "知识产权条款", "suggestion": ""},
            {"clause_type": "termination", "risk_level": "high", "status": "open", "description": "解除终止条款", "suggestion": ""},
            {"clause_type": "dispute_resolution", "risk_level": "medium", "status": "open", "description": "争议解决条款", "suggestion": ""},
            {"clause_type": "breach", "risk_level": "high", "status": "open", "description": "合同未约定违约责任条款，建议补充", "suggestion": "补充违约金条款"},
        ]
        result = eval_contract_review_case(case, (risks, f"共识别 8 项审查提示，其中高风险 3 项。 {DISCLAIMER}"))
        self.assertTrue(result["pass"], result)
        self.assertEqual(result["missing_clause_recall"], 1.0)

    def test_exact_status_still_works(self):
        case = {
            "id": "cr_x",
            "category": "risky",
            "gold": {
                "expected_present_clauses": ["payment"],
                "expected_absent_clauses": ["breach"],
                "min_high_risk_count": 1,
            },
        }
        risks = [
            {"clause_type": "payment", "risk_level": "medium", "status": "open", "description": "付款安排", "suggestion": ""},
            {"clause_type": "breach", "risk_level": "high", "status": "needs_facts", "description": "合同未约定违约责任条款", "suggestion": ""},
        ]
        result = eval_contract_review_case(case, (risks, f"共识别 2 项审查提示。 {DISCLAIMER}"))
        self.assertTrue(result["pass"], result)


if __name__ == "__main__":
    unittest.main()
