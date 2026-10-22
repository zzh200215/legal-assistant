"""咨询侧证据链摘要：引用/句级溯源/忠实度 → 等级（读时派生，不写库）。"""

import json
import unittest

from app.models.legal import LegalConsultation
from app.services.legal.consultation_evidence import summarize_consultation_evidence
from app.services.legal.legal_workspace_service import serialize_workspace_row


def _consultation(**overrides):
    row = LegalConsultation(
        user_id=1, question="公司辞退我是否要补偿？", category="labor",
        known_facts_json="[]", missing_facts_json="[]", references_json="[]",
        advice="可以主张经济补偿。", risk_level="medium", status="draft",
    )
    for key, value in overrides.items():
        setattr(row, key, json.dumps(value, ensure_ascii=False))
    return row


def _attribution(total, matched):
    return {
        "granularity": "sentence",
        "total_sentences": total,
        "matched_sentences": matched,
        "sentences": [],
    }


class SummarizeConsultationEvidenceTests(unittest.TestCase):
    def test_no_references_is_none(self):
        summary = summarize_consultation_evidence(_consultation())
        self.assertEqual(summary["references"], 0)
        self.assertEqual(summary["level"], "none")
        self.assertEqual(summary["faithfulness"], "unchecked")

    def test_strong_when_coverage_high_and_refs_active(self):
        row = _consultation(
            references_json=[{"title": "劳动合同法", "status": "active"}],
            attribution_json=_attribution(5, 4),
            faithfulness_json={"available": True, "faithful": True, "reason": "ok"},
        )
        summary = summarize_consultation_evidence(row)
        self.assertEqual(summary["sentences"], 5)
        self.assertEqual(summary["attributed"], 4)
        self.assertEqual(summary["attributed_rate"], 0.8)
        self.assertEqual(summary["faithfulness"], "pass")
        self.assertEqual(summary["level"], "strong")

    def test_partial_when_coverage_between_half_and_strong(self):
        row = _consultation(
            references_json=[{"title": "劳动合同法", "status": "active"}],
            attribution_json=_attribution(10, 6),
        )
        self.assertEqual(summarize_consultation_evidence(row)["level"], "partial")

    def test_fail_faithfulness_forces_weak(self):
        row = _consultation(
            references_json=[{"title": "劳动合同法", "status": "active"}],
            attribution_json=_attribution(5, 5),
            faithfulness_json={"available": True, "faithful": False, "unsupported": ["一句无依据"]},
        )
        summary = summarize_consultation_evidence(row)
        self.assertEqual(summary["faithfulness"], "fail")
        self.assertEqual(summary["unsupported"], 1)
        self.assertEqual(summary["level"], "weak")

    def test_legacy_without_attribution_is_partial_not_strong(self):
        row = _consultation(references_json=[{"title": "劳动合同法", "status": "active"}])
        summary = summarize_consultation_evidence(row)
        self.assertEqual(summary["sentences"], 0)
        self.assertIsNone(summary["attributed_rate"])
        self.assertEqual(summary["level"], "partial")

    def test_inactive_references_cannot_be_strong(self):
        row = _consultation(
            references_json=[{"title": "已废止法", "status": "inactive"}],
            attribution_json=_attribution(3, 3),
        )
        self.assertEqual(summarize_consultation_evidence(row)["level"], "partial")

    def test_serializer_attaches_summary(self):
        row = _consultation(
            references_json=[{"title": "劳动合同法", "status": "active"}],
            attribution_json=_attribution(4, 4),
        )
        payload = serialize_workspace_row(row)
        self.assertIn("evidence_summary", payload)
        self.assertEqual(payload["evidence_summary"]["level"], "strong")
        self.assertEqual(payload["evidence_summary"]["references"], 1)
