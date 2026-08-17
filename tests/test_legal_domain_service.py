"""P1 领域服务测试：结论层级 / 发布门禁 / 风险项状态机 / 版本绑定与追溯。

覆盖需求验收点：三类 claim 区分(2)、无依据结论不可发布(3)、风险项完整字段(6)、
高严重度未审核不可发布(7)、审核后修改需重审(8)、追溯完整(9)、非法转换被拒(12)。
"""
import asyncio
import json
import unittest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import hash_password
from app.core.database import Base
from app.models.legal import (
    ContractReview,
    LegalCase,
    LegalConsultation,
    LegalDraft,
    LegalDocumentVersion,
    LegalReviewAction,
    LegalSource,
)
from app.models.legal_domain import (
    ContractRiskItem,
    LegalClaim,
    LegalEvidence,
    LegalFact,
    LegalReference,
)
from app.models.org import Organization, OrganizationMember
from app.models.user import User
from app.services.legal.legal_domain_service import (
    assert_publishable,
    persist_consultation_artifacts,
    persist_draft_artifacts,
    persist_review_artifacts,
    update_risk_item_status,
)
from app.services.legal.legal_workspace_service import (
    LegalWorkspaceModule,
    LegalWorkspaceReadModule,
)

HIGH_RISK = {
    "clause_type": "breach", "label": "违约责任", "risk_level": "high",
    "description": "违约金过高", "source_location": {"paragraph": 1, "snippet": "甲方违约需支付100%违约金"},
    "suggestion": "降低违约金", "status": "open",
}
LOW_RISK = {
    "clause_type": "payment", "label": "付款条款", "risk_level": "low",
    "description": "付款节点不明确", "source_location": {},
    "suggestion": "明确付款节点", "status": "open",
}


class LegalDomainServiceTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        self.db = self.SessionLocal()

        self.user = User(username="lawyer", email="law@x.com", hashed_password=hash_password("pw"), role="admin")
        self.db.add(self.user)
        self.org = Organization(name="律所X", code="firm_x")
        self.db.add(self.org)
        self.db.commit()
        self.db.refresh(self.user)
        self.db.refresh(self.org)
        self.db.add(OrganizationMember(organization_id=self.org.id, user_id=self.user.id, legal_role="reviewer"))

        self.case = LegalCase(organization_id=self.org.id, user_id=self.user.id,
                              title="劳动争议案", case_type="labor_dispute")
        self.db.add(self.case)
        self.source = LegalSource(
            user_id=self.user.id, title="民法典", source_type="statute", content="合同编",
            status="active", effective_date=date(2020, 1, 1), jurisdiction="中国大陆",
        )
        self.db.add(self.source)
        self.db.commit()
        self.db.refresh(self.case)
        self.db.refresh(self.source)

        self.read_module = LegalWorkspaceReadModule(audit=MagicMock())
        self.workspace_module = LegalWorkspaceModule(audit=MagicMock())
        self.ref = {"source_id": self.source.id, "title": "民法典", "citation": "民法典合同编",
                    "version": "v1", "status": "active", "effective_date": "2020-01-01",
                    "jurisdiction": "中国大陆"}

    def tearDown(self):
        self.db.close()

    def _create_review(self, status="pending_review"):
        review = ContractReview(
            user_id=self.user.id, case_id=self.case.id, title="服务合同", content="合同正文",
            summary="", risks_json="[]", references_json="[]", status=status,
        )
        self.db.add(review)
        self.db.commit()
        self.db.refresh(review)
        return review

    # ── 验收6：风险项完整字段 + 高严重度自动进入审核队列 ────────────────────────

    def test_review_artifacts_risk_items_with_complete_fields(self):
        review = self._create_review()
        persist_review_artifacts(self.db, review, risks=[HIGH_RISK, LOW_RISK], refs=[self.ref])

        items = self.db.query(ContractRiskItem).filter(ContractRiskItem.review_id == review.id).all()
        self.assertEqual(len(items), 2)
        by_sev = {i.severity: i for i in items}
        # 模型输出不自动确认；高严重度进入审核队列
        self.assertEqual(by_sev["high"].status, "needs_review")
        self.assertEqual(by_sev["low"].status, "open")
        self.assertEqual(by_sev["high"].category, "breach")
        self.assertEqual(by_sev["high"].recommendation, "降低违约金")
        self.assertIn("snippet", json.loads(by_sev["high"].evidence_json))
        self.assertIn("100%", by_sev["high"].original_text_excerpt)
        self.assertEqual(by_sev["high"].source, "model")

        # 原始模型快照
        self.db.refresh(review)
        self.assertIn("model", json.loads(review.model_snapshot_json))

        # Claim -> Evidence 链：高严重度风险有 risk_warning claim + 可定位证据
        claims = self.db.query(LegalClaim).filter(
            LegalClaim.source_type == "contract_review", LegalClaim.source_id == review.id,
        ).all()
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].claim_type, "risk_warning")
        self.assertEqual(claims[0].status, "needs_review")
        evidences = self.db.query(LegalEvidence).filter(LegalEvidence.claim_id == claims[0].id).all()
        self.assertEqual(len(evidences), 1)
        self.assertEqual(evidences[0].kind, "support")

        # Claim -> Reference 链：引用持久化且适用性已判定
        refs = self.db.query(LegalReference).filter(LegalReference.source_id == self.source.id).all()
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].applicable, 1)

    # ── 验收2：三类 claim 被正确区分 ───────────────────────────────────────────

    def test_claim_types_distinguished_in_consultation(self):
        consult = LegalConsultation(
            user_id=self.user.id, case_id=self.case.id, question="被辞退怎么办", category="labor_dispute",
            known_facts_json="[]", missing_facts_json="[]", references_json="[]",
            advice="一般建议", risk_level="high", status="needs_lawyer_review",
        )
        self.db.add(consult)
        self.db.commit()
        self.db.refresh(consult)
        persist_consultation_artifacts(
            self.db, consult, known=["在公司工作3年"], missing=["劳动关系起止时间"], refs=[self.ref], risk_level="high",
        )
        facts = self.db.query(LegalFact).filter(LegalFact.consultation_id == consult.id).all()
        self.assertEqual(len(facts), 2)
        self.assertEqual({f.fact_type for f in facts}, {"known", "missing"})
        claims = self.db.query(LegalClaim).filter(LegalClaim.source_type == "consultation").all()
        types = {c.claim_type for c in claims}
        self.assertIn("fact_to_confirm", types)   # 缺失事实 → 待确认
        self.assertIn("risk_warning", types)      # 高风险 → 风险提示
        self.assertNotIn("legal_conclusion", types)  # 一般建议不被标为确定结论

    # ── 验收3：无法源依据的法律结论不可发布 ─────────────────────────────────────

    def test_conclusion_without_source_cannot_publish(self):
        review = self._create_review()
        persist_review_artifacts(self.db, review, risks=[LOW_RISK], refs=[])
        # 人为构造一个无依据的 legal_conclusion claim
        self.db.add(LegalClaim(
            organization_id=self.org.id, user_id=self.user.id, case_id=self.case.id,
            source_type="contract_review", source_id=review.id, claim_type="legal_conclusion",
            statement="应支付经济补偿金3万元", status="unsupported", source="model",
        ))
        self.db.commit()
        self.read_module.apply_review_action(
            self.db, self.user, target_type="contract_review", target_id=review.id,
            action="approve", note="ok",
        )
        verdict = assert_publishable(self.db, self.user, "contract_review", review.id)
        self.assertFalse(verdict["ok"])
        self.assertTrue(any("无法源依据" in r for r in verdict["reasons"]))

    # ── 验收7：高/严重风险项未审核不可发布 ──────────────────────────────────────

    def test_publish_gate_blocks_unreviewed_high_risk(self):
        review = self._create_review()
        persist_review_artifacts(self.db, review, risks=[HIGH_RISK], refs=[self.ref])
        # 未审核
        verdict = assert_publishable(self.db, self.user, "contract_review", review.id)
        self.assertFalse(verdict["ok"])
        # 审核通过但高风险仍未处理
        self.read_module.apply_review_action(
            self.db, self.user, target_type="contract_review", target_id=review.id,
            action="approve", note="ok",
        )
        verdict = assert_publishable(self.db, self.user, "contract_review", review.id)
        self.assertFalse(verdict["ok"])
        self.assertTrue(any("风险项" in r for r in verdict["reasons"]))
        # 处理后可通过
        item = self.db.query(ContractRiskItem).filter(
            ContractRiskItem.review_id == review.id, ContractRiskItem.severity == "high",
        ).first()
        update_risk_item_status(self.db, self.user, item.id, "accept", "已接受该风险")
        verdict = assert_publishable(self.db, self.user, "contract_review", review.id)
        self.assertTrue(verdict["ok"])

    def test_publish_gate_not_approved_blocked(self):
        review = self._create_review()
        persist_review_artifacts(self.db, review, risks=[LOW_RISK], refs=[self.ref])
        verdict = assert_publishable(self.db, self.user, "contract_review", review.id)
        self.assertFalse(verdict["ok"])
        self.assertTrue(any("未审核通过" in r for r in verdict["reasons"]))

    # ── 验收6：风险项状态机记录操作者/时间/原因 ────────────────────────────────

    def test_risk_item_status_transition_records_reviewer(self):
        review = self._create_review()
        persist_review_artifacts(self.db, review, risks=[HIGH_RISK], refs=[])
        item = self.db.query(ContractRiskItem).first()
        result = update_risk_item_status(self.db, self.user, item.id, "dismiss", "不属于本所负责范围")
        self.assertEqual(result["status"], "dismissed")
        self.assertEqual(result["reviewer_id"], self.user.id)
        self.assertIsNotNone(result["resolved_at"])
        self.assertEqual(result["resolution_note"], "不属于本所负责范围")
        # 关联的 risk_warning claim 同步为已确认
        claim = self.db.query(LegalClaim).filter(LegalClaim.risk_item_id == item.id).first()
        self.assertEqual(claim.status, "approved")

    def test_risk_item_invalid_action_rejected(self):
        review = self._create_review()
        persist_review_artifacts(self.db, review, risks=[LOW_RISK], refs=[])
        item = self.db.query(ContractRiskItem).first()
        with self.assertRaises(ValueError):
            update_risk_item_status(self.db, self.user, item.id, "explode", None)

    # ── 验收8：内容修改产生新版本并要求重新审核 ─────────────────────────────────

    def test_modification_creates_new_version_and_requires_reapproval(self):
        draft = LegalDraft(
            user_id=self.user.id, case_id=self.case.id, document_type="labor_arbitration_application",
            title="仲裁申请书", fields_json="{}", missing_fields_json='["申请人"]',
            references_json="[]", content="旧草稿", version=1, status="returned_for_facts",
        )
        self.db.add(draft)
        self.db.commit()
        self.db.refresh(draft)
        persist_draft_artifacts(self.db, draft, missing_fields=["申请人"], refs=[])
        old_claims = self.db.query(LegalClaim).filter(LegalClaim.source_type == "draft",
                                                      LegalClaim.source_id == draft.id).all()
        self.assertEqual(len(old_claims), 1)
        self.assertEqual(old_claims[0].claim_type, "fact_to_confirm")

        with patch("app.services.legal.legal_workspace_service.draft_content",
                   new=AsyncMock(return_value="修改后的新草稿")):
            asyncio.run(self.workspace_module.resubmit_draft(
                self.db, self.user, draft_id=draft.id,
                document_type="labor_arbitration_application",
                fields={"申请人": "张三", "被申请人": "某公司", "仲裁请求": "支付补偿金",
                        "事实与理由": "事实", "证据清单": "证据"},
            ))
        self.db.refresh(draft)
        self.assertEqual(draft.version, 2)
        self.assertEqual(draft.status, "pending_review")   # 修改后回到待审核
        self.assertIsNone(draft.reviewed_version)
        # 旧版本未决 claim 被取代
        self.db.refresh(old_claims[0])
        self.assertEqual(old_claims[0].status, "superseded")
        # 新版本产生新 claim
        new_claims = self.db.query(LegalClaim).filter(
            LegalClaim.source_type == "draft", LegalClaim.source_id == draft.id,
            LegalClaim.status != "superseded",
        ).all()
        self.assertGreaterEqual(len(new_claims), 1)
        # 版本快照留痕
        snapshots = self.db.query(LegalDocumentVersion).filter(
            LegalDocumentVersion.target_type == "draft", LegalDocumentVersion.target_id == draft.id,
        ).all()
        self.assertGreaterEqual(len(snapshots), 1)

    # ── 验收9：原始模型结果/人工修改/审核/最终版可追溯 ──────────────────────────

    def test_traceability_chain(self):
        review = self._create_review()
        persist_review_artifacts(self.db, review, risks=[LOW_RISK], refs=[self.ref])
        self.db.refresh(review)
        # 原始模型结果快照
        self.assertIn("model", json.loads(review.model_snapshot_json))
        # 审核（approve）绑定版本并冻结快照
        self.read_module.apply_review_action(
            self.db, self.user, target_type="contract_review", target_id=review.id,
            action="approve", note="通过",
        )
        self.db.refresh(review)
        self.assertEqual(review.status, "lawyer_approved")
        self.assertEqual(review.reviewed_version, 1)
        snapshot = self.db.query(LegalDocumentVersion).filter(
            LegalDocumentVersion.target_type == "contract_review",
            LegalDocumentVersion.target_id == review.id,
            LegalDocumentVersion.snapshot_reason == "manual",
        ).first()
        self.assertIsNotNone(snapshot)
        # 审核动作记录目标版本
        action = self.db.query(LegalReviewAction).filter(
            LegalReviewAction.target_type == "contract_review",
            LegalReviewAction.target_id == review.id,
            LegalReviewAction.action == "approve",
        ).first()
        self.assertEqual(action.target_version, 1)

    def test_consultation_return_from_approved_does_not_crash(self):
        # 咨询无 is_final 列：已审咨询被退回不应因缺列崩溃，且审核绑定被清除。
        consult = LegalConsultation(
            user_id=self.user.id, case_id=self.case.id, question="q", category="labor_dispute",
            known_facts_json="[]", missing_facts_json="[]", references_json="[]",
            advice="a", risk_level="medium", status="needs_lawyer_review",
        )
        self.db.add(consult)
        self.db.commit()
        self.db.refresh(consult)
        self.read_module.apply_review_action(
            self.db, self.user, target_type="consultation", target_id=consult.id,
            action="approve", note="通过",
        )
        self.read_module.apply_review_action(
            self.db, self.user, target_type="consultation", target_id=consult.id,
            action="return", note="需补充证据",
        )
        self.db.refresh(consult)
        self.assertEqual(consult.status, "returned_for_facts")

    # ── 验收12：非法状态转换被服务端拒绝 ────────────────────────────────────────

    def test_illegal_transition_rejected(self):
        review = self._create_review()
        self.read_module.apply_review_action(
            self.db, self.user, target_type="contract_review", target_id=review.id,
            action="approve", note="通过",
        )
        # 已审核通过后再次 approve / 所有者 submit_review → 拒绝
        with self.assertRaises(ValueError):
            self.read_module.apply_review_action(
                self.db, self.user, target_type="contract_review", target_id=review.id,
                action="approve", note="再通过",
            )
        with self.assertRaises(ValueError):
            self.read_module.apply_review_action(
                self.db, self.user, target_type="contract_review", target_id=review.id,
                action="submit_review", note="再提交",
            )

    # ── 验收8：approved -> superseded：已审内容被退回修订后须重新审核 ────────────

    def test_approved_reopened_for_revision_requires_reapproval(self):
        review = self._create_review()
        persist_review_artifacts(self.db, review, risks=[LOW_RISK], refs=[self.ref])
        self.read_module.apply_review_action(
            self.db, self.user, target_type="contract_review", target_id=review.id,
            action="approve", note="通过",
        )
        self.db.refresh(review)
        self.assertEqual(review.reviewed_version, 1)
        self.assertTrue(assert_publishable(self.db, self.user, "contract_review", review.id)["ok"])

        # 律师退回已审内容 → 审批失效，不可再发布
        self.read_module.apply_review_action(
            self.db, self.user, target_type="contract_review", target_id=review.id,
            action="return", note="需补充违约金上限约定",
        )
        self.db.refresh(review)
        self.assertEqual(review.status, "returned_for_facts")
        self.assertIsNone(review.reviewed_version)
        self.assertEqual(review.is_final, 0)
        self.assertFalse(assert_publishable(self.db, self.user, "contract_review", review.id)["ok"])

        # 修改后重提（新版本）→ 重新审核
        with patch("app.services.legal.legal_workspace_service.review_contract",
                   new=AsyncMock(return_value=([LOW_RISK], "新摘要"))):
            asyncio.run(self.workspace_module.resubmit_contract_review(
                self.db, self.user, review_id=review.id, title="服务合同", content="新正文",
            ))
        self.db.refresh(review)
        self.assertEqual(review.version, 2)
        self.assertEqual(review.status, "pending_review")
        self.assertIsNone(review.reviewed_version)
        # 旧版本风险项被取代
        old_items = self.db.query(ContractRiskItem).filter(
            ContractRiskItem.review_id == review.id, ContractRiskItem.status == "dismissed",
        ).all()
        self.assertGreaterEqual(len(old_items), 1)
        # 重新审核通过后可发布
        self.read_module.apply_review_action(
            self.db, self.user, target_type="contract_review", target_id=review.id,
            action="approve", note="重新通过",
        )
        self.db.refresh(review)
        self.assertEqual(review.reviewed_version, 2)
        self.assertTrue(assert_publishable(self.db, self.user, "contract_review", review.id)["ok"])


if __name__ == "__main__":
    unittest.main()
