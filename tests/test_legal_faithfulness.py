"""生成后忠实度校验（copilot 第四层）：判官补判、落库、可重判、不可用不落库。

覆盖：
- `_faithfulness_evidence`：优先「被引用的条文」，其次该法源已收录条文，再退法源正文，
  最后退引用自身标题+条号；无文本的引用跳过（不塞空片段让判官误判「有依据」）。
- `LegalWorkspaceModule.check_consultation_faithfulness`：判官入参与结果落库；已判记录直接回读
  （不再花钱调判官）；`refresh=True` 重判；判官不可用时**不落库**；他人记录按「不存在」处理。
"""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import hash_password
from app.core.database import Base
from app.models.legal import ContractReview, LegalArticle, LegalConsultation, LegalDraft, LegalSource
from app.models.user import User, UserStatus
from app.services.legal.legal_workspace_service import (
    _faithfulness_evidence,
    legal_workspace_module,
)


def _run(coro):
    return asyncio.run(coro)


class FaithfulnessEvidenceTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        self.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        self.db = self.SessionLocal()
        self.user = self._user("evidence")
        self.source = LegalSource(
            user_id=self.user.id, title="中华人民共和国劳动合同法", source_type="statute",
            citation="主席令第73号", status="active", content="法源摘要正文", full_text="",
        )
        self.db.add(self.source)
        self.db.flush()
        self.db.add_all([
            LegalArticle(source_id=self.source.id, article_number="第47条", content="经济补偿按劳动者工作年限支付。", sequence=1),
            LegalArticle(source_id=self.source.id, article_number="第87条", content="违法解除应支付二倍赔偿金。", sequence=2),
        ])
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _user(self, name):
        user = User(
            username=name, email=f"{name}@test.com",
            hashed_password=hash_password("pw"), status=UserStatus.active.value,
        )
        self.db.add(user)
        self.db.flush()
        return user

    def _row(self, references):
        row = LegalConsultation(
            user_id=self.user.id, question="被违法解除可以主张什么？", advice="可以主张赔偿金。",
            category="labor", risk_level="medium", status="pending_review",
            references_json=json.dumps(references, ensure_ascii=False),
        )
        self.db.add(row)
        self.db.commit()
        return row

    def test_no_references_returns_empty(self):
        row = self._row([])
        self.assertEqual(_faithfulness_evidence(self.db, row), [])

    def test_prefers_cited_articles_only(self):
        row = self._row([{
            "source_id": self.source.id, "title": "劳动合同法", "citation": "第87条",
        }])
        chunks = _faithfulness_evidence(self.db, row)
        self.assertEqual(len(chunks), 1)
        self.assertIn("第87条", chunks[0]["content"])
        self.assertIn("二倍赔偿金", chunks[0]["content"])
        # 没被引用的第 47 条不应出现：判官只看「本次引用支撑了什么」
        self.assertNotIn("第47条", chunks[0]["content"])

    def test_falls_back_to_all_articles_when_no_numbers(self):
        row = self._row([{"source_id": self.source.id, "title": "劳动合同法", "citation": "劳动合同法"}])
        content = _faithfulness_evidence(self.db, row)[0]["content"]
        self.assertIn("第47条", content)
        self.assertIn("第87条", content)

    def test_falls_back_to_source_body_without_articles(self):
        bare = LegalSource(
            user_id=self.user.id, title="劳动争议调解仲裁法", source_type="statute",
            citation="主席令第80号", status="active", content="仲裁时效为一年。", full_text="",
        )
        self.db.add(bare)
        self.db.commit()
        row = self._row([{"source_id": bare.id, "title": "调解仲裁法", "citation": "第27条"}])
        content = _faithfulness_evidence(self.db, row)[0]["content"]
        self.assertIn("仲裁时效为一年。", content)

    def test_falls_back_to_reference_text_without_source(self):
        row = self._row([{"title": "某地方性法规", "citation": "第5条"}])
        chunks = _faithfulness_evidence(self.db, row)
        self.assertEqual(len(chunks), 1)
        self.assertIn("某地方性法规", chunks[0]["content"])

    def test_skips_reference_without_any_text(self):
        row = self._row([{"source_id": None, "title": "", "citation": ""}])
        self.assertEqual(_faithfulness_evidence(self.db, row), [])


class CheckConsultationFaithfulnessTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        self.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        self.db = self.SessionLocal()
        self.owner = self._user("owner")
        self.other = self._user("other")
        self.source = LegalSource(
            user_id=self.owner.id, title="中华人民共和国劳动合同法", source_type="statute",
            citation="主席令第73号", status="active", content="", full_text="",
        )
        self.db.add(self.source)
        self.db.flush()
        self.db.add(LegalArticle(
            source_id=self.source.id, article_number="第87条", content="违法解除应支付二倍赔偿金。", sequence=1,
        ))
        self.row = LegalConsultation(
            user_id=self.owner.id, question="被违法解除可以主张什么？",
            advice="可以主张二倍赔偿金。", category="labor", references_json=json.dumps(
                [{"source_id": self.source.id, "title": "劳动合同法", "citation": "第87条"}], ensure_ascii=False,
            ),
            status="pending_review",
        )
        self.db.add(self.row)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _user(self, name):
        user = User(
            username=name, email=f"{name}@test.com",
            hashed_password=hash_password("pw"), status=UserStatus.active.value,
        )
        self.db.add(user)
        self.db.flush()
        return user

    def test_verdict_is_persisted_and_returned_with_metadata(self):
        verdict = {"available": True, "faithful": True, "unsupported": [], "reason": "全部有据"}
        with patch(
            "app.services.legal.legal_workspace_service.check_faithfulness",
            new=AsyncMock(return_value=verdict),
        ) as judge:
            result = _run(legal_workspace_module.check_consultation_faithfulness(
                self.db, self.owner, self.row.id))
        self.assertTrue(result["faithful"])
        self.assertEqual(result["evidence_count"], 1)
        self.assertTrue(result["checked_at"])
        self.assertEqual(judge.await_count, 1)
        # 入参：问题、答复、证据片段
        args, kwargs = judge.await_args
        self.assertEqual(args[0], "被违法解除可以主张什么？")
        self.assertEqual(args[1], "可以主张二倍赔偿金。")
        self.assertIn("二倍赔偿金", args[2][0]["content"])
        self.assertEqual(kwargs["user_id"], self.owner.id)

        self.db.refresh(self.row)
        stored = json.loads(self.row.faithfulness_json)
        self.assertTrue(stored["faithful"])
        self.assertEqual(stored["evidence_count"], 1)

    def test_stored_verdict_is_reused_without_calling_judge(self):
        self.row.faithfulness_json = json.dumps(
            {"available": True, "faithful": False, "unsupported": ["二倍赔偿金"], "reason": "证据里没有"},
            ensure_ascii=False,
        )
        self.db.commit()
        with patch(
            "app.services.legal.legal_workspace_service.check_faithfulness",
            new=AsyncMock(side_effect=AssertionError("不应重复调用判官")),
        ):
            result = _run(legal_workspace_module.check_consultation_faithfulness(
                self.db, self.owner, self.row.id))
        self.assertFalse(result["faithful"])
        self.assertEqual(result["unsupported"], ["二倍赔偿金"])

    def test_refresh_rejudges(self):
        self.row.faithfulness_json = json.dumps({"available": True, "faithful": False}, ensure_ascii=False)
        self.db.commit()
        with patch(
            "app.services.legal.legal_workspace_service.check_faithfulness",
            new=AsyncMock(return_value={"available": True, "faithful": True, "unsupported": [], "reason": "重判通过"}),
        ) as judge:
            result = _run(legal_workspace_module.check_consultation_faithfulness(
                self.db, self.owner, self.row.id, refresh=True))
        self.assertTrue(result["faithful"])
        self.assertEqual(judge.await_count, 1)

    def test_unavailable_judge_is_not_persisted(self):
        with patch(
            "app.services.legal.legal_workspace_service.check_faithfulness",
            new=AsyncMock(return_value={"available": False, "reason": "faithfulness_unavailable", "faithful": None, "unsupported": []}),
        ):
            result = _run(legal_workspace_module.check_consultation_faithfulness(
                self.db, self.owner, self.row.id))
        self.assertFalse(result["available"])
        self.db.refresh(self.row)
        # 不落库：把「判官挂了」固化成结论比不判更误导，留空才能下次重试
        self.assertIsNone(self.row.faithfulness_json)

    def test_other_users_consultation_is_not_found(self):
        with self.assertRaises(LookupError) as raised:
            _run(legal_workspace_module.check_consultation_faithfulness(self.db, self.other, self.row.id))
        self.assertEqual(str(raised.exception), "LEGAL_CONSULTATION_NOT_FOUND")

    def test_serialize_exposes_faithfulness(self):
        from app.services.legal.legal_workspace_service import serialize_workspace_row

        self.assertEqual(serialize_workspace_row(self.row)["faithfulness"], {})
        self.row.faithfulness_json = json.dumps({"available": True, "faithful": True}, ensure_ascii=False)
        self.db.commit()
        self.assertTrue(serialize_workspace_row(self.row)["faithfulness"]["faithful"])


class MaterialContextEvidenceTests(unittest.TestCase):
    """带材料提问时，材料正文必须进判官证据（否则合同/文书里的细节会被判成无依据）。"""

    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        self.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        self.db = self.SessionLocal()
        self.owner = self._user("ctx-owner")
        self.other = self._user("ctx-other")
        self.review = ContractReview(
            user_id=self.owner.id, title="技术服务合同（演示）",
            content="签约后5日内支付100万元，阶段验收后支付108万元。", status="needs_lawyer_review",
        )
        self.draft = LegalDraft(
            user_id=self.owner.id, title="劳动争议仲裁申请书", document_type="labor_arbitration_application",
            content="仲裁请求：请求裁决被申请人支付经济补偿金。", status="pending_review",
        )
        self.db.add_all([self.review, self.draft])
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _user(self, name):
        user = User(
            username=name, email=f"{name}@test.com",
            hashed_password=hash_password("pw"), status=UserStatus.active.value,
        )
        self.db.add(user)
        self.db.commit()
        return user

    def _row(self, contexts, user=None):
        row = LegalConsultation(
            user_id=(user or self.owner).id, question="这份合同的付款期限是怎么约定的？",
            advice="签约后5日内支付100万元。", category="contract", status="pending_review",
            references_json="[]",
            trace_json=json.dumps({"contexts": contexts}, ensure_ascii=False),
        )
        self.db.add(row)
        self.db.commit()
        return row

    def test_contract_material_becomes_evidence(self):
        from app.services.legal.legal_workspace_service import _material_context_evidence

        row = self._row([{"type": "contract_review", "id": self.review.id, "title": self.review.title, "chars": 30}])
        chunks = _material_context_evidence(self.db, self.owner, row)
        self.assertEqual(len(chunks), 1)
        self.assertIn("合同审查 技术服务合同（演示）", chunks[0]["content"])
        self.assertIn("签约后5日内支付100万元", chunks[0]["content"])

    def test_draft_material_becomes_evidence(self):
        from app.services.legal.legal_workspace_service import _material_context_evidence

        row = self._row([{"type": "draft", "id": self.draft.id, "title": self.draft.title, "chars": 20}])
        chunks = _material_context_evidence(self.db, self.owner, row)
        self.assertEqual(len(chunks), 1)
        self.assertIn("文书草稿 劳动争议仲裁申请书", chunks[0]["content"])

    def test_without_contexts_returns_empty(self):
        from app.services.legal.legal_workspace_service import _material_context_evidence

        self.assertEqual(_material_context_evidence(self.db, self.owner, self._row([])), [])
        self.assertEqual(_material_context_evidence(self.db, self.owner, self._row(None)), [])

    def test_other_users_material_is_skipped(self):
        from app.services.legal.legal_workspace_service import _material_context_evidence

        row = self._row(
            [{"type": "contract_review", "id": self.review.id, "title": self.review.title, "chars": 30}],
            user=self.other,
        )
        # 材料属于 owner，但咨询属于 other：不得把他人材料塞进证据
        self.assertEqual(_material_context_evidence(self.db, self.other, row), [])

    def test_unknown_or_deleted_material_is_skipped(self):
        from app.services.legal.legal_workspace_service import _material_context_evidence

        row = self._row([
            {"type": "contract_review", "id": 999999, "title": "已删除合同", "chars": 10},
            {"type": "unknown_kind", "id": 1, "title": "别的类型", "chars": 10},
        ])
        self.assertEqual(_material_context_evidence(self.db, self.owner, row), [])

    def test_check_includes_material_context(self):
        row = self._row(
            [{"type": "contract_review", "id": self.review.id, "title": self.review.title, "chars": 30}],
        )
        with patch(
            "app.services.legal.legal_workspace_service.check_faithfulness",
            new=AsyncMock(return_value={"available": True, "faithful": True, "unsupported": [], "reason": "有依据"}),
        ) as judge:
            result = _run(legal_workspace_module.check_consultation_faithfulness(self.db, self.owner, row.id))
        self.assertTrue(result["faithful"])
        chunks = judge.await_args.args[2]
        joined = "\n".join(chunk["content"] for chunk in chunks)
        self.assertIn("签约后5日内支付100万元", joined)
        self.assertEqual(result["evidence_count"], len(chunks))


if __name__ == "__main__":
    unittest.main()
