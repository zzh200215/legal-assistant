"""合同审查三层评审的规则层口径：定位率、snippet 可核验、引用状态与去重。"""

import json
import unittest
from types import SimpleNamespace

from scripts.run_contract_review_review import (
    aggregate_rules,
    dedupe_reviews,
    evaluate_summary_claims,
    extract_summary_claims,
    normalize_quality,
    rule_metrics,
)


def _row(**overrides):
    base = {
        "id": 1,
        "user_id": 1,
        "title": "测试合同",
        "status": "needs_lawyer_review",
        "summary": "摘要",
        "risks_json": "[]",
        "references_json": "[]",
        "review_policy_snapshot_json": "{}",
        "created_at": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _risk(*, level="high", clause="payment", paragraph=1, snippet="", suggestion="补"):
    return {
        "clause_type": clause,
        "label": "付款条款",
        "risk_level": level,
        "description": "描述",
        "source_location": {"paragraph": paragraph, "snippet": snippet},
        "suggestion": suggestion,
    }


class RuleMetricsTest(unittest.TestCase):
    def test_anchor_and_snippet_are_measured_separately(self):
        content = "甲方应于验收后付款。乙方应交付成果。"
        risks = [
            _risk(paragraph=1, snippet="甲方应于验收后付款"),
            _risk(paragraph=None, snippet="", suggestion=""),
        ]
        row = _row(risks_json=json.dumps(risks, ensure_ascii=False))
        metrics = rule_metrics(row, content)

        self.assertEqual(metrics["risks"], 2)
        self.assertEqual(metrics["anchored"], 1)
        self.assertEqual(metrics["anchor_rate"], 0.5)
        self.assertEqual(metrics["snippets"], 1)
        self.assertEqual(metrics["snippet_verified"], 1)
        self.assertEqual(metrics["suggestions"], 1)

    def test_snippet_not_in_content_is_not_verified(self):
        risks = [_risk(paragraph=1, snippet="合同里没有这句话")]
        row = _row(risks_json=json.dumps(risks, ensure_ascii=False))
        metrics = rule_metrics(row, "甲方应付款。")
        self.assertEqual(metrics["snippet_verified"], 0)
        self.assertEqual(metrics["snippet_verified_rate"], 0.0)

    def test_reference_status_is_counted(self):
        refs = [
            {"source_id": 1, "verification": {"current_effective": True, "superseded": False}},
            {"source_id": 2, "verification": {"current_effective": False, "superseded": True}},
            {"title": "无核验字段"},
        ]
        row = _row(references_json=json.dumps(refs, ensure_ascii=False))
        metrics = rule_metrics(row, "正文")
        self.assertEqual(metrics["refs"], 3)
        self.assertEqual(metrics["refs_with_source"], 2)
        self.assertEqual(metrics["refs_current"], 1)
        self.assertEqual(metrics["refs_superseded"], 1)
        self.assertEqual(metrics["refs_unverified"], 1)

    def test_policy_snapshot_only_counts_non_empty(self):
        self.assertFalse(rule_metrics(_row(review_policy_snapshot_json="{}"), "")["has_policy_snapshot"])
        self.assertTrue(rule_metrics(_row(review_policy_snapshot_json='{"party_role": "甲方"}'), "")["has_policy_snapshot"])


class SummaryClaimsTest(unittest.TestCase):
    """摘要自述（识别 N 类条款 / N 项高风险）必须与清单对得上——零模型可核。"""

    RISKS = [
        _risk(level="high", clause="payment"),
        _risk(level="medium", clause="breach"),
        _risk(level="low", clause="dispute_resolution"),
    ]

    def test_claims_are_extracted_with_arabic_and_chinese_numerals(self):
        self.assertEqual(extract_summary_claims("共识别8类条款")["claimed_clause_types"], 8)
        self.assertEqual(extract_summary_claims("共识别八类条款")["claimed_clause_types"], 8)
        self.assertEqual(extract_summary_claims("共识别7项高风险")["claimed_high"], 7)
        self.assertEqual(extract_summary_claims("共识别 12 项审查提示")["claimed_risks"], 12)
        self.assertIsNone(extract_summary_claims("本合同缺失交付条款")["claimed_clause_types"])

    def test_overclaimed_coverage_is_flagged(self):
        # 真实案例 contract-1：摘要称 8 类，清单只有 3 类（两个裁判都放过的那种错）
        result = evaluate_summary_claims("共识别8类条款，其中付款条款存在风险。", self.RISKS)
        self.assertTrue(result["checked"])
        self.assertFalse(result["consistent"])
        self.assertEqual(result["actual_clause_types"], 3)
        self.assertIn("8 类条款", result["mismatches"][0])

    def test_consistent_claim_passes(self):
        result = evaluate_summary_claims("共识别7项高风险事项。", self.RISKS)
        self.assertTrue(result["checked"])
        self.assertFalse(result["consistent"])  # 实际 high 只有 1 条
        ok = evaluate_summary_claims("共识别1项高风险事项。", self.RISKS)
        self.assertTrue(ok["consistent"])

    def test_no_claim_is_not_counted(self):
        result = evaluate_summary_claims("本合同结构不完整，建议补充条款。", self.RISKS)
        self.assertFalse(result["checked"])
        self.assertIsNone(result["consistent"])
        self.assertEqual(result["mismatches"], [])

    def test_rule_metrics_carries_claims(self):
        row = _row(summary="共识别8类条款", risks_json=json.dumps(self.RISKS, ensure_ascii=False))
        metrics = rule_metrics(row, "正文")
        self.assertEqual(metrics["summary_claims"]["claimed_clause_types"], 8)
        self.assertFalse(metrics["summary_claims"]["consistent"])


class AggregateTest(unittest.TestCase):
    def test_aggregate_rates_use_risk_denominator(self):
        content = "甲方应付款。"
        rows = [
            _row(id=1, risks_json=json.dumps([_risk(paragraph=1, snippet="甲方应付款")], ensure_ascii=False)),
            _row(id=2, risks_json=json.dumps([_risk(paragraph=None, snippet="", suggestion="")], ensure_ascii=False)),
        ]
        rules = [rule_metrics(row, content) for row in rows]
        agg = aggregate_rules(rules)

        self.assertEqual(agg["reviews"], 2)
        self.assertEqual(agg["risks_total"], 2)
        self.assertEqual(agg["anchor_rate"], 0.5)
        self.assertEqual(agg["reviews_without_anchor"], 1)
        self.assertEqual(agg["suggestion_rate"], 0.5)

    def test_aggregate_counts_summary_claim_mismatches(self):
        content = "甲方应付款。"
        rows = [
            _row(id=1, summary="共识别8类条款", risks_json=json.dumps([_risk(clause="payment")], ensure_ascii=False)),
            _row(id=2, summary="共识别1类条款", risks_json=json.dumps([_risk(clause="payment")], ensure_ascii=False)),
            _row(id=3, summary="无数字自述", risks_json=json.dumps([_risk(clause="payment")], ensure_ascii=False)),
        ]
        agg = aggregate_rules([rule_metrics(row, content) for row in rows])
        self.assertEqual(agg["summary_claims_checked"], 2)
        self.assertEqual(agg["summary_claims_consistent"], 1)
        self.assertEqual([item["review_id"] for item in agg["summary_claims_mismatches"]], [1])


class DedupeTest(unittest.TestCase):
    def test_same_content_and_risks_keeps_first(self):
        rows = [
            _row(id=1, risks_json='[{"a": 1}]'),
            _row(id=2, risks_json='[{"a": 1}]'),
            _row(id=3, risks_json='[{"a": 2}]'),
        ]
        rows[0].content = rows[1].content = "同一段正文"
        rows[2].content = "同一段正文"
        kept, coverage = dedupe_reviews(rows)

        self.assertEqual([row.id for row in kept], [1, 3])
        self.assertEqual(coverage["dropped_duplicate_ids"], [2])
        self.assertEqual(coverage["reviews_total"], 3)
        self.assertEqual(coverage["reviews_unique"], 2)


class NormalizeQualityTest(unittest.TestCase):
    def test_clamps_and_whitelists_verdict(self):
        result = normalize_quality({"coverage": 2, "grounding": -1, "actionability": "0.5", "verdict": "PASS"})
        self.assertEqual(result["coverage"], 1.0)
        self.assertEqual(result["grounding"], 0.0)
        self.assertEqual(result["actionability"], 0.5)
        self.assertEqual(result["verdict"], "pass")

    def test_unknown_verdict_and_missing_dims(self):
        result = normalize_quality({"verdict": "maybe"})
        self.assertIsNone(result["coverage"])
        self.assertEqual(result["verdict"], "review")


if __name__ == "__main__":
    unittest.main()
