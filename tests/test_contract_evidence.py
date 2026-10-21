"""合同审查证据链：风险 → 原文定位的服务端统计（读时计算，不写库）。

口径必须与前端 `useContractEvidence`、评测脚本 `run_contract_review_review` 一致：
`paragraph` 非空 = 有定位；snippet 去空白后前 80 字是正文子串 = 可核验；无 snippet 不进分母。
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from app.models.legal import ContractReview
from app.services.legal.contract_evidence import annotate_contract_evidence
from app.services.legal.legal_workspace_service import serialize_workspace_row

CONTENT = "合同总金额为人民币一百万元。\n双方未约定交付标准。"


def _risk(*, paragraph=1, snippet="合同总金额为人民币一百万元", level="high", clause="payment"):
    return {
        "clause_type": clause,
        "risk_level": level,
        "description": "描述",
        "source_location": {"paragraph": paragraph, "snippet": snippet},
    }


class AnnotateContractEvidenceTest(unittest.TestCase):
    def test_empty_risks_yield_none_level(self):
        risks, summary = annotate_contract_evidence(CONTENT, [])
        self.assertEqual(risks, [])
        self.assertEqual(summary["total"], 0)
        self.assertEqual(summary["level"], "none")
        self.assertEqual(summary["located_rate"], 0.0)
        self.assertIsNone(summary["snippet_rate"])

    def test_all_anchored_is_strong(self):
        risks, summary = annotate_contract_evidence(CONTENT, [_risk(), _risk(paragraph=2, snippet="双方未约定交付标准")])
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["anchored"], 2)
        self.assertEqual(summary["unanchored"], 0)
        self.assertEqual(summary["level"], "strong")
        self.assertTrue(risks[0]["evidence"]["anchored"])
        self.assertTrue(risks[1]["evidence"]["snippet_verified"])

    def test_missing_snippet_is_not_counted_in_denominator(self):
        risks, summary = annotate_contract_evidence(CONTENT, [_risk(), _risk(paragraph=2, snippet="")])
        self.assertIsNone(risks[1]["evidence"]["snippet_verified"])
        self.assertEqual(summary["snippets"], 1)
        self.assertEqual(summary["verified"], 1)
        self.assertEqual(summary["snippet_rate"], 1.0)

    def test_snippet_not_in_content_is_flagged(self):
        risks, summary = annotate_contract_evidence(CONTENT, [_risk(snippet="这句话不在合同里")])
        self.assertFalse(risks[0]["evidence"]["snippet_verified"])
        self.assertEqual(summary["verified"], 0)
        self.assertEqual(summary["snippet_rate"], 0.0)

    def test_snippet_match_ignores_whitespace(self):
        risks, _ = annotate_contract_evidence(CONTENT, [_risk(snippet="合同总金额 为人民币 一百万元")])
        self.assertTrue(risks[0]["evidence"]["snippet_verified"])

    def test_level_thresholds(self):
        two_of_three = [_risk(), _risk(paragraph=2, snippet=""), _risk(paragraph=None, snippet="")]
        _, summary = annotate_contract_evidence(CONTENT, two_of_three)
        self.assertEqual(summary["level"], "partial")

        one_of_three = [_risk(), _risk(paragraph=None, snippet=""), _risk(paragraph=None, snippet="")]
        _, summary = annotate_contract_evidence(CONTENT, one_of_three)
        self.assertEqual(summary["level"], "weak")

        half = [_risk(), _risk(paragraph=None, snippet="")]
        _, summary = annotate_contract_evidence(CONTENT, half)
        self.assertEqual(summary["level"], "partial")  # 一半有定位按 partial，不是 weak

    def test_malformed_input_is_tolerated(self):
        risks, summary = annotate_contract_evidence(CONTENT, [{"clause_type": "payment"}, "not-a-dict", None])
        self.assertEqual(len(risks), 1)
        self.assertEqual(summary["total"], 1)
        self.assertFalse(risks[0]["evidence"]["anchored"])

        risks, summary = annotate_contract_evidence(CONTENT, "oops")
        self.assertEqual(risks, [])
        self.assertEqual(summary["level"], "none")

    def test_input_risks_are_not_mutated(self):
        original = _risk()
        snapshot = json.dumps(original, ensure_ascii=False, sort_keys=True)
        annotate_contract_evidence(CONTENT, [original])
        self.assertEqual(json.dumps(original, ensure_ascii=False, sort_keys=True), snapshot)


class SerializeWorkspaceRowTest(unittest.TestCase):
    def _row(self, risks):
        row = ContractReview(
            id=7,
            user_id=1,
            title="测试合同",
            content=CONTENT,
            summary="摘要",
            risks_json=json.dumps(risks, ensure_ascii=False),
            references_json="[]",
            status="needs_lawyer_review",
            version=1,
        )
        return row

    def test_contract_review_payload_carries_evidence(self):
        row = self._row([_risk(), _risk(paragraph=None, snippet="")])
        with patch("app.services.legal.legal_workspace_service.compute_confidence", return_value=0.5):
            payload = serialize_workspace_row(row)

        self.assertEqual(payload["evidence_summary"]["total"], 2)
        self.assertEqual(payload["evidence_summary"]["anchored"], 1)
        self.assertEqual(payload["evidence_summary"]["level"], "partial")
        self.assertTrue(payload["risks"][0]["evidence"]["anchored"])
        self.assertFalse(payload["risks"][1]["evidence"]["anchored"])
        # 原有字段不受影响
        self.assertEqual(payload["title"], "测试合同")
        self.assertEqual(len(payload["risks"]), 2)


if __name__ == "__main__":
    unittest.main()
