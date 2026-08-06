import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.models.legal import ContractReview
from app.services.legal_workspace_service import (
    LegalWorkspaceModule,
    LegalWorkspaceReadModule,
    compute_confidence,
    serialize_workspace_row,
)


class ComputeConfidenceTests(unittest.TestCase):
    def test_consultation_with_active_sources_and_no_missing_scores_high(self):
        consultation = SimpleNamespace(
            references_json='[{"status": "active", "title": "《劳动合同法》"}]',
            missing_facts_json="[]", risk_level="low",
        )
        score = compute_confidence(consultation)
        self.assertGreaterEqual(score, 70)
        self.assertLessEqual(score, 95)

    def test_consultation_many_missing_facts_scores_lower(self):
        consultation = SimpleNamespace(
            references_json="[]", missing_facts_json='["a","b","c"]', risk_level="high",
        )
        self.assertLess(compute_confidence(consultation), 55)

    def test_draft_missing_fields_reduces_confidence(self):
        draft = SimpleNamespace(
            document_type="labor_arbitration_application",
            missing_fields_json='["申请人","被申请人","金额"]',
        )
        self.assertLessEqual(compute_confidence(draft), 60)

    def test_serialize_exposes_confidence_and_feedback_score(self):
        row = ContractReview(
            id=7, user_id=3, title="服务合同", content="合同正文", document_id=None, version=2,
            status="needs_lawyer_review", summary="存在付款风险", risks_json="[]",
            references_json="[]", review_policy_id=None, review_policy_version=None,
            review_policy_snapshot_json=None, reviewer_id=None, review_note=None,
            reviewed_at=None, feedback_score=1,
        )
        payload = serialize_workspace_row(row)
        self.assertIn("confidence", payload)
        self.assertEqual(payload["feedback_score"], 1)


class LegalWorkspaceReadModuleTests(unittest.TestCase):
    def setUp(self):
        self.audit = MagicMock()
        self.module = LegalWorkspaceReadModule(audit=self.audit)
        self.db = MagicMock()

    def test_contract_serialization_preserves_workspace_shape(self):
        row = ContractReview(
            id=7, user_id=3, title="服务合同", content="合同正文", document_id=None, version=2,
            status="needs_lawyer_review", summary="存在付款风险", risks_json="[]",
            references_json="[]", review_policy_id=None, review_policy_version=None,
            review_policy_snapshot_json=None, reviewer_id=None, review_note=None,
            reviewed_at=None,
        )
        payload = serialize_workspace_row(row)
        self.assertEqual(payload["id"], 7)
        self.assertEqual(payload["version"], 2)
        self.assertEqual(payload["risks"], [])
        self.assertEqual(payload["review_policy_snapshot"], {})
        self.assertIn("case_id", payload)

    def test_owner_can_submit_own_record_for_review(self):
        row = SimpleNamespace(id=19, user_id=3, status="pending_review")
        user = SimpleNamespace(id=3, role="user")
        with patch("app.services.legal_workspace_service.target_query", return_value=row), \
             patch("app.services.legal_workspace_service.serialize_workspace_row", return_value={"id": 19, "status": "needs_lawyer_review"}):
            result = self.module.apply_review_action(
                self.db, user, target_type="consultation", target_id=19,
                action="submit_review", note=None,
            )
        self.assertEqual(result["status"], "needs_lawyer_review")
        self.assertEqual(row.status, "needs_lawyer_review")
        self.db.commit.assert_called_once()
        self.audit.log.assert_called_once()

    def test_non_reviewer_cannot_approve(self):
        row = SimpleNamespace(id=19, user_id=3, status="pending_review")
        user = SimpleNamespace(id=3, role="user")
        with patch("app.services.legal_workspace_service.target_query", return_value=row):
            with self.assertRaisesRegex(PermissionError, "LEGAL_REVIEW_FORBIDDEN"):
                self.module.apply_review_action(
                    self.db, user, target_type="consultation", target_id=19,
                    action="approve", note="ok",
                )
        self.db.commit.assert_not_called()

    def test_unknown_review_action_is_rejected_before_write(self):
        row = SimpleNamespace(id=19, user_id=3, status="pending_review")
        user = SimpleNamespace(id=3, role="user")
        with patch("app.services.legal_workspace_service.target_query", return_value=row):
            with self.assertRaisesRegex(ValueError, "LEGAL_REVIEW_ACTION_INVALID"):
                self.module.apply_review_action(
                    self.db, user, target_type="consultation", target_id=19,
                    action="replace_everything", note=None,
                )
        self.db.commit.assert_not_called()


class LegalWorkspaceModuleCaseTests(unittest.TestCase):
    def setUp(self):
        self.module = LegalWorkspaceModule(audit=MagicMock())
        self.db = MagicMock()

    def test_resolve_case_id_none_returns_none(self):
        user = SimpleNamespace(organization_id=2)
        self.assertIsNone(self.module._resolve_case_id(self.db, user, None))

    @patch("app.services.legal_workspace_service.verify_case_access")
    def test_resolve_case_id_accepts_same_org(self, mock_verify):
        user = SimpleNamespace(id=1, organization_id=2)
        # verify_case_access 通过（不抛错）→ 返回 case_id
        self.assertEqual(self.module._resolve_case_id(self.db, user, 5), 5)
        mock_verify.assert_called_once_with(5, user.id, self.db)

    @patch("app.services.legal_workspace_service.verify_case_access")
    def test_resolve_case_id_rejects_foreign_org(self, mock_verify):
        user = SimpleNamespace(id=1, organization_id=2)
        from fastapi import HTTPException
        mock_verify.side_effect = HTTPException(404)
        with self.assertRaisesRegex(LookupError, "LEGAL_CASE_NOT_FOUND"):
            self.module._resolve_case_id(self.db, user, 5)


if __name__ == "__main__":
    unittest.main()
