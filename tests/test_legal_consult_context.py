"""咨询上下文注入：面板把「当前正在看的合同/文书」交给服务端。

覆盖：
- `_prepare_extra_context`：脱敏、截断、元数据兜底；拿不到正文一律 None。
- `_load_consult_context`：归属与案件权限口径（本人 / 同案件成员 / 无权一律按不存在）。
- `consultation_payload(extra_context=...)`：只拼进问题段、不参与法源排序，trace 顶层
  `contexts` 如实记录；不传时 prompt 与 trace 与从前逐字一致。
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import hash_password
from app.core.database import Base
from app.models.legal import ContractReview, LegalCase, LegalDraft
from app.models.legal_portal import LegalCaseMember
from app.models.org import LegalMemberRole, Organization, OrganizationMember
from app.models.user import User, UserStatus
from app.services.legal.legal_service import (
    CONTEXT_MAX_CHARS,
    _prepare_extra_context,
    consultation_payload,
)
from app.services.legal.legal_workspace_service import legal_workspace_module


def _source(source_id=1, title="中华人民共和国劳动合同法", citation="主席令第73号", version="2012修正"):
    return SimpleNamespace(
        id=source_id, title=title, citation=citation, version=version,
        status="active", user_id=1, content="", full_text="",
    )


class PrepareExtraContextTests(unittest.TestCase):
    def test_missing_or_empty_text_returns_none(self):
        self.assertIsNone(_prepare_extra_context(None))
        self.assertIsNone(_prepare_extra_context("不是字典"))
        self.assertIsNone(_prepare_extra_context({}))
        self.assertIsNone(_prepare_extra_context({"text": ""}))
        self.assertIsNone(_prepare_extra_context({"text": "   \n  "}))
        self.assertIsNone(_prepare_extra_context({"text": 123}))

    def test_metadata_defaults_and_passthrough(self):
        meta = _prepare_extra_context({"text": "合同正文", "id": 12, "title": "服务合同"})
        self.assertEqual(meta["type"], "context")
        self.assertEqual(meta["type_label"], "参考材料")
        self.assertEqual(meta["id"], 12)
        self.assertEqual(meta["title"], "服务合同")
        self.assertEqual(meta["chars"], 4)
        self.assertEqual(meta["text"], "合同正文")

    def test_truncates_to_context_max_chars(self):
        meta = _prepare_extra_context({"text": "条" * (CONTEXT_MAX_CHARS + 500)})
        self.assertEqual(meta["chars"], CONTEXT_MAX_CHARS)
        self.assertEqual(len(meta["text"]), CONTEXT_MAX_CHARS)


class LoadConsultContextTests(unittest.TestCase):
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

        self.org = Organization(name="律所A", code="ORG_CTX")
        self.db.add(self.org)
        self.db.flush()
        self.owner = self._user("owner")
        self.other = self._user("other")
        self.member = self._user("member")
        self.case = LegalCase(
            organization_id=self.org.id, user_id=self.owner.id,
            title="案件A", case_type="other", is_strict_mode=0,
        )
        self.db.add(self.case)
        self.db.flush()
        self.db.add(OrganizationMember(organization_id=self.org.id, user_id=self.member.id, legal_role="editor"))
        self.db.add(LegalCaseMember(
            case_id=self.case.id, organization_id=self.org.id, user_id=self.member.id,
            case_role="collaborator", granted_by=self.owner.id,
        ))
        self.db.flush()

        self.review = ContractReview(
            user_id=self.owner.id, case_id=self.case.id, title="服务合同",
            content="甲方应在 30 日内付款。", status="pending_review",
        )
        self.draft = LegalDraft(
            user_id=self.owner.id, case_id=self.case.id, title="劳动仲裁申请书",
            document_type="labor_arbitration_application", content="申请人：张三。", status="draft",
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
        self.db.flush()
        return user

    def test_owner_contract_review_is_loaded(self):
        ctx = legal_workspace_module._load_consult_context(
            self.db, self.owner, contract_review_id=self.review.id)
        self.assertEqual(ctx["type"], "contract_review")
        self.assertEqual(ctx["type_label"], "合同审查")
        self.assertEqual(ctx["id"], self.review.id)
        self.assertEqual(ctx["title"], "服务合同")
        self.assertEqual(ctx["text"], "甲方应在 30 日内付款。")

    def test_owner_draft_is_loaded(self):
        ctx = legal_workspace_module._load_consult_context(self.db, self.owner, draft_id=self.draft.id)
        self.assertEqual(ctx["type"], "draft")
        self.assertEqual(ctx["type_label"], "文书草稿")
        self.assertEqual(ctx["text"], "申请人：张三。")

    def test_no_context_requested_returns_none(self):
        self.assertIsNone(legal_workspace_module._load_consult_context(self.db, self.owner))

    def test_both_ids_conflict(self):
        with self.assertRaises(ValueError) as raised:
            legal_workspace_module._load_consult_context(
                self.db, self.owner, contract_review_id=self.review.id, draft_id=self.draft.id)
        self.assertEqual(str(raised.exception), "LEGAL_CONTEXT_CONFLICT")

    def test_missing_row_is_not_found(self):
        with self.assertRaises(LookupError) as raised:
            legal_workspace_module._load_consult_context(self.db, self.owner, contract_review_id=999999)
        self.assertEqual(str(raised.exception), "LEGAL_CONTEXT_NOT_FOUND")

    def test_other_user_without_case_access_is_not_found(self):
        with self.assertRaises(LookupError) as raised:
            legal_workspace_module._load_consult_context(
                self.db, self.other, contract_review_id=self.review.id)
        self.assertEqual(str(raised.exception), "LEGAL_CONTEXT_NOT_FOUND")

    def test_case_member_can_reference_others_row(self):
        ctx = legal_workspace_module._load_consult_context(
            self.db, self.member, contract_review_id=self.review.id)
        self.assertEqual(ctx["id"], self.review.id)


class ConsultationPayloadContextTests(unittest.TestCase):
    def _run(self, *, extra_context=None, trace_out=None):
        captured = {}

        def fake_render(name, **kwargs):
            captured.update(kwargs)
            return "prompt"

        with patch("app.services.legal.legal_service.prompt_service.render_by_name", side_effect=fake_render), \
             patch("app.services.legal.legal_service._llm_chat", new=AsyncMock(return_value=None)):
            asyncio.run(consultation_payload(
                "试用期最长多久？", [_source()], user_id=1,
                trace_out=trace_out, extra_context=extra_context,
            ))
        return captured

    def test_context_is_appended_to_question_only(self):
        trace = {}
        captured = self._run(
            extra_context={"type": "contract_review", "type_label": "合同审查", "id": 12,
                           "title": "服务合同", "text": "甲方应在 30 日内付款。"},
            trace_out=trace,
        )
        question = captured["question"]
        self.assertTrue(question.startswith("试用期最长多久？"))
        self.assertIn("参考上下文（合同审查《服务合同》#12", question)
        self.assertIn("甲方应在 30 日内付款。", question)
        self.assertIn("它不是法源", question)
        # 法源列表仍只由法源构成，上下文没有混进检索结果
        self.assertNotIn("服务合同", captured["source_list"])
        self.assertEqual(trace["contexts"], [{
            "type": "contract_review", "id": 12, "title": "服务合同",
            "chars": len("甲方应在 30 日内付款。"),
        }])

    def test_without_context_prompt_and_trace_stay_unchanged(self):
        trace = {}
        captured = self._run(trace_out=trace)
        self.assertEqual(captured["question"], "试用期最长多久？")
        self.assertNotIn("参考上下文", captured["question"])
        self.assertNotIn("contexts", trace)

    def test_empty_context_text_is_not_injected(self):
        trace = {}
        captured = self._run(extra_context={"id": 12, "title": "服务合同", "text": ""}, trace_out=trace)
        self.assertEqual(captured["question"], "试用期最长多久？")
        self.assertNotIn("contexts", trace)

    def test_refusal_path_records_no_context(self):
        trace = {}
        captured = {}
        with patch("app.services.legal.legal_service.prompt_service.render_by_name",
                   side_effect=lambda name, **kwargs: captured.update(kwargs) or "prompt"):
            asyncio.run(consultation_payload(
                "教我伪造合同骗取银行贷款，有什么办法？", [_source()], user_id=1, trace_out=trace,
                extra_context={"id": 12, "title": "服务合同", "text": "甲方应在 30 日内付款。"},
            ))
        self.assertEqual([step["id"] for step in trace["steps"]], ["refuse"])
        self.assertNotIn("contexts", trace)
        self.assertEqual(captured, {})


if __name__ == "__main__":
    unittest.main()
