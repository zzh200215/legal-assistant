"""AI-2: 审核反馈回流评测闭环 — 抽取逻辑测试。"""
import unittest
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.legal import (
    ContractReview,
    LegalConsultation,
    LegalDraft,
    LegalReviewAction,
)
from scripts.export_review_feedback import build_case, extract_ai_output, extract_source, run


class ExportReviewFeedbackTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        self.session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()

        self.consultation = LegalConsultation(
            user_id=1, question="公司辞退我未支付经济补偿金怎么办？",
            category="labor_dispute", advice="可申请劳动仲裁，主张经济补偿。",
            risk_level="medium", status="pending_review", references_json="[]",
            known_facts_json="[]", missing_facts_json="[]",
        )
        self.session.add(self.consultation)
        self.session.flush()

        self.contract = ContractReview(
            user_id=1, title="技术服务合同", content="验收后30日付款，逾期按日万分之三付违约金。",
            summary="付款条款存在逾期责任不明确风险。", status="pending_review",
            risks_json='[{"risk_level":"high","label":"违约责任","description":"违约金计算方式未明确"}]',
            references_json="[]",
        )
        self.session.add(self.contract)
        self.session.flush()

        self.draft = LegalDraft(
            user_id=1, document_type="labor_arbitration_application", title="劳动争议仲裁申请书",
            content="申请人：张三。仲裁请求：支付经济补偿金。", status="needs_facts",
            fields_json='{"申请人":"张三"}', missing_fields_json='["被申请人"]',
            references_json="[]",
        )
        self.session.add(self.draft)
        self.session.flush()

    def _add_action(self, target_type, target_id, action, note="", action_id=None):
        record = LegalReviewAction(
            reviewer_id=9, target_type=target_type, target_id=target_id,
            action=action, note=note, from_status="pending_review",
            to_status={"approve": "lawyer_approved", "return": "returned_for_facts",
                       "offline": "offline_consultation", "close": "archived"}[action],
            created_at=datetime(2026, 8, 2, 8, 0, 0, tzinfo=timezone.utc),
        )
        self.session.add(record)
        self.session.flush()
        if action_id is not None:
            record.id = action_id
        return record.id

    def test_extract_source_per_type(self):
        self.assertIn("公司辞退我", extract_source(self.consultation, "consultation"))
        self.assertIn("技术服务合同", extract_source(self.contract, "contract_review"))
        self.assertIn("张三", extract_source(self.draft, "draft"))

    def test_extract_ai_output_per_type(self):
        self.assertIn("劳动仲裁", extract_ai_output(self.consultation, "consultation"))
        self.assertIn("违约金计算方式未明确", extract_ai_output(self.contract, "contract_review"))
        self.assertIn("支付经济补偿金", extract_ai_output(self.draft, "draft"))

    def test_build_case_pairs_review_decision_with_ai_output(self):
        self._add_action("consultation", self.consultation.id, "return", note="缺少劳动关系起止时间")
        action = self.session.query(LegalReviewAction).one()
        case = build_case("consultation", self.consultation, action)
        self.assertEqual(case["id"], f"rf-consultation-{self.consultation.id}")
        self.assertEqual(case["review_action"], "return")
        self.assertEqual(case["review_note"], "缺少劳动关系起止时间")
        self.assertEqual(case["to_status"], "returned_for_facts")
        self.assertIn("公司辞退我", case["source"])
        self.assertIn("劳动仲裁", case["ai_output"])

    def test_run_exports_actions_after_cursor(self):
        first = self._add_action("consultation", self.consultation.id, "return", note="缺事实", action_id=1)
        self._add_action("contract_review", self.contract.id, "approve", note="ok", action_id=2)
        self._add_action("draft", self.draft.id, "offline", note="转线下", action_id=3)

        result = run(self.session, after_id=0)
        self.assertEqual(result["stats"]["exported"], 3)
        self.assertEqual(result["stats"]["by_action"], {"return": 1, "approve": 1, "offline": 1})
        self.assertEqual(result["last_action_id"], 3)

        # 增量：从 last_action_id 继续，无新增
        incremental = run(self.session, after_id=result["last_action_id"])
        self.assertEqual(incremental["stats"]["exported"], 0)
        self.assertEqual(incremental["last_action_id"], 3)

    def test_run_deduplicates_same_target_keeps_latest(self):
        self._add_action("consultation", self.consultation.id, "return", note="第一次退回", action_id=1)
        self._add_action("consultation", self.consultation.id, "approve", note="修订后通过", action_id=2)
        result = run(self.session, after_id=0)
        self.assertEqual(result["stats"]["exported"], 1)
        self.assertEqual(result["cases"][0]["review_action"], "approve")
        self.assertEqual(result["cases"][0]["review_note"], "修订后通过")

    def test_run_filters_actions_and_skips_missing_targets(self):
        self._add_action("consultation", self.consultation.id, "close", note="归档", action_id=1)
        self._add_action("consultation", 99999, "return", note="目标已删", action_id=2)
        result = run(self.session, after_id=0)
        self.assertEqual(result["stats"]["exported"], 0)  # close 被过滤、目标缺失被跳过
        self.assertEqual(result["stats"]["skipped_missing_targets"], 1)


if __name__ == "__main__":
    unittest.main()
