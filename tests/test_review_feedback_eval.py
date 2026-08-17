"""AI-2: 审核反馈回流评测闭环 — 转换与端到端回归测试。

覆盖：回流用例 → 评测 dataset 转换（按审核决策推断结构性回归断言），
以及 12 条模拟审核决策的端到端回流（导出 → 转换 → 确定性评测全过）。
"""
import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.legal import ContractReview, LegalConsultation, LegalDraft, LegalReviewAction
from eval.load_review_feedback import to_dataset_cases
from scripts.export_review_feedback import build_case, run


def _build_db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


class LoadReviewFeedbackTests(unittest.TestCase):
    def test_to_dataset_consultation_return_infers_missing_facts_assertion(self):
        case = {
            "id": "rf-consultation-1", "target_type": "consultation", "target_id": 1,
            "source": "公司拖欠工资怎么办", "review_action": "return",
        }
        dataset = to_dataset_cases([case])
        self.assertEqual(len(dataset["consultation_cases"]), 1)
        gold = dataset["consultation_cases"][0]["gold"]
        self.assertTrue(gold["skip_category_check"])
        self.assertTrue(gold["must_have_missing_facts"], "return 用例必须断言缺失事实被标注")
        self.assertEqual(gold["risk_level_min"], "low")

    def test_to_dataset_consultation_offline_raises_risk_min(self):
        case = {
            "id": "rf-consultation-2", "target_type": "consultation", "target_id": 2,
            "source": "被起诉了怎么办", "review_action": "offline",
        }
        dataset = to_dataset_cases([case])
        self.assertEqual(dataset["consultation_cases"][0]["gold"]["risk_level_min"], "medium")

    def test_to_dataset_contract_structural_only(self):
        case = {
            "id": "rf-contract-1", "target_type": "contract_review", "target_id": 1,
            "source": "甲方与乙方签订合同", "review_action": "approve",
        }
        dataset = to_dataset_cases([case])
        self.assertEqual(len(dataset["contract_review_cases"]), 1)
        self.assertTrue(dataset["contract_review_cases"][0]["gold"]["structural_only"])

    def test_to_dataset_draft_keeps_document_type(self):
        case = {
            "id": "rf-draft-1", "target_type": "draft", "target_id": 1,
            "document_type": "labor_arbitration_application",
            "source": "劳动争议仲裁申请书\n申请人: 张三", "review_action": "approve",
        }
        dataset = to_dataset_cases([case])
        self.assertEqual(len(dataset["draft_generation_cases"]), 1)
        self.assertEqual(
            dataset["draft_generation_cases"][0]["document_type"],
            "labor_arbitration_application",
        )
        self.assertTrue(dataset["draft_generation_cases"][0]["gold"]["must_contain_disclaimer"])

    def test_to_dataset_ignores_unknown_target_type(self):
        dataset = to_dataset_cases([{"id": "x", "target_type": "meeting", "review_action": "approve"}])
        self.assertEqual(
            sum(len(v) for v in dataset.values()), 0,
            "未知 target_type 不应生成评测用例",
        )


class EndToEndFeedbackLoopTests(unittest.TestCase):
    """端到端：12 条模拟审核决策 → 导出 → 转换 → 确定性评测回归全过。"""

    def _seed(self, db, *, consultation_count=4, contract_count=4, draft_count=4):
        rows = []
        for i in range(consultation_count):
            row = LegalConsultation(
                user_id=1, question=f"公司拖欠工资第{i + 1}个月怎么办",
                category="labor_dispute", advice="可申请劳动仲裁，主张经济补偿。",
                risk_level="medium", status="pending_review", references_json="[]",
                known_facts_json="[]", missing_facts_json="[]",
            )
            db.add(row)
            rows.append(("consultation", row))
        for i in range(contract_count):
            row = ContractReview(
                user_id=1, title=f"服务合同{i + 1}", content=f"第{i + 1}份合同：验收后30日付款，逾期按日万分之三付违约金。",
                summary="付款条款存在逾期责任不明确风险。", status="pending_review",
                risks_json='[{"risk_level":"high","label":"违约责任","description":"违约金计算方式未明确"}]',
                references_json="[]",
            )
            db.add(row)
            rows.append(("contract_review", row))
        for i in range(draft_count):
            row = LegalDraft(
                user_id=1, document_type="labor_arbitration_application", title="劳动争议仲裁申请书",
                content="申请人：张三。仲裁请求：支付经济补偿金。", status="needs_facts",
                fields_json='{"申请人":"张三"}', missing_fields_json='["被申请人"]',
                references_json="[]",
            )
            db.add(row)
            rows.append(("draft", row))
        db.commit()
        return rows

    def _review(self, db, target_type, row, action, note):
        db.add(LegalReviewAction(
            reviewer_id=9, target_type=target_type, target_id=row.id,
            action=action, note=note, from_status="pending_review",
            to_status={"approve": "lawyer_approved", "return": "returned_for_facts",
                       "offline": "offline_consultation"}[action],
            created_at=datetime(2026, 8, 3, 10, 0, 0, tzinfo=timezone.utc),
        ))
        db.commit()

    def test_review_feedback_loop_passes_on_deterministic_path(self):
        db = _build_db()
        rows = self._seed(db)
        actions = ["approve", "return", "offline"] * 4
        for index, (target_type, row) in enumerate(rows):
            self._review(db, target_type, row, actions[index], note=f"律师意见 {index}")

        exported = run(db, after_id=0)["cases"]
        self.assertGreaterEqual(len(exported), 12, "首批抽取应 ≥ 12 题")

        dataset = to_dataset_cases(exported)
        self.assertGreaterEqual(len(dataset["consultation_cases"]), 4)
        self.assertGreaterEqual(len(dataset["contract_review_cases"]), 4)
        self.assertGreaterEqual(len(dataset["draft_generation_cases"]), 4)

        from eval.run_generation_eval import run_eval

        async def _no_llm(*_args, **_kwargs):
            return None

        async def runner():
            with patch("app.services.legal.legal_service._llm_chat", new=_no_llm):
                return await run_eval(dataset, db)

        report = asyncio.run(runner())

        # 确定性路径下，回流回归用例全部通过（结构断言 + 免责声明 + 无虚构）
        for r in report["consultation"]["cases"]:
            self.assertTrue(r.get("pass"), f"consultation case {r['case_id']} failed: {r}")
        for r in report["contract_review"]["cases"]:
            self.assertTrue(r.get("pass"), f"contract case {r['case_id']} failed: {r}")
        for r in report["draft_generation"]["cases"]:
            self.assertTrue(r.get("pass"), f"draft case {r['case_id']} failed: {r}")


if __name__ == "__main__":
    unittest.main()
