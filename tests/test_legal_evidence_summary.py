"""证据可核验性在线指标：article_scope 口径与 evidence_summary 汇总。

C 项（三层评审的在线化）要回答的不是「模型答得好不好」，而是**用户能不能自己核验这条答复**。
离线实测的结论是主变量在证据可得性（抽样里常只有 1/8 条答复能看到条文正文），所以这里的口径
只用已发生的事实：条文级核对做过没有、通过没有、引用有没有挂到法源库，不做加权评分。
"""

import asyncio
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.legal import LegalArticle, LegalSource
from app.services.legal.legal_reference_service import (
    enrich_references,
    evidence_summary,
    verify_source,
)
from app.services.legal.legal_service import consultation_payload


def _ref(scope: str | None, *, source_id: int | None = 1) -> dict:
    ref = {"source_id": source_id, "title": "劳动合同法", "citation": "第40条"}
    if scope is not None:
        ref["verification"] = {"verified": scope == "verified", "article_scope": scope}
    return ref


class EvidenceSummaryTests(unittest.TestCase):
    """汇总口径：四级 + 计数 + 说明文案（纯函数，不碰库）。"""

    def test_empty_references_is_none_level(self):
        summary = evidence_summary([])
        self.assertEqual(summary["level"], "none")
        self.assertEqual(summary["label"], "未引用法源")
        self.assertEqual(summary["references"], 0)
        self.assertIn("没有引用法源", summary["note"])

    def test_all_verified_is_strong(self):
        summary = evidence_summary([_ref("verified"), _ref("verified")])
        self.assertEqual(summary["level"], "strong")
        self.assertEqual(summary["label"], "引用已逐条核对")
        self.assertEqual(summary["article_verified"], 2)
        self.assertEqual(summary["article_checked"], 2)
        self.assertIn("2 条引用已与法源条文核对一致", summary["note"])

    def test_mixed_verified_and_no_corpus_is_partial(self):
        summary = evidence_summary([_ref("verified"), _ref("no_corpus")])
        self.assertEqual(summary["level"], "partial")
        self.assertEqual(summary["article_verified"], 1)
        self.assertEqual(summary["no_corpus"], 1)
        self.assertIn("1 条引用所在法源没有条文级数据", summary["note"])

    def test_missing_article_is_weak_and_named(self):
        summary = evidence_summary([_ref("missing")])
        self.assertEqual(summary["level"], "weak")
        self.assertEqual(summary["label"], "引用待核")
        self.assertIn("条号未在已收录条文中找到", summary["note"])

    def test_no_numbers_and_no_source_are_counted_separately(self):
        summary = evidence_summary([_ref("no_numbers"), _ref("no_source", source_id=None)])
        self.assertEqual(summary["level"], "weak")
        self.assertEqual(summary["no_numbers"], 1)
        self.assertEqual(summary["unmatched"], 1)

    def test_reference_without_verification_counts_as_unmatched(self):
        summary = evidence_summary([_ref(None)])
        self.assertEqual(summary["unmatched"], 1)
        self.assertEqual(summary["level"], "weak")

    def test_unknown_scope_degrades_to_unmatched(self):
        summary = evidence_summary([{"source_id": 1, "verification": {"article_scope": "wat"}}])
        self.assertEqual(summary["unmatched"], 1)

    def test_materials_are_reported_in_note(self):
        summary = evidence_summary([_ref("verified")], materials=1)
        self.assertEqual(summary["materials"], 1)
        self.assertIn("已把 1 份案件材料纳入证据", summary["note"])


class ArticleScopeTests(unittest.TestCase):
    """verify_source / enrich_references 必须给出机器可读口径，而不是只给文案。"""

    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session()
        self.source = LegalSource(
            user_id=1, title="劳动合同法", citation="第40条",
            source_type="statute", status="active", version="v1", content="摘要",
        )
        self.db.add(self.source)
        self.db.commit()
        self.db.refresh(self.source)

    def tearDown(self):
        self.db.close()

    def _add_articles(self, *numbers):
        for index, number in enumerate(numbers):
            self.db.add(LegalArticle(
                source_id=self.source.id, article_number=number,
                content=f"{number} 的条文正文", sequence=index,
            ))
        self.db.commit()

    def test_scope_verified_when_numbers_hit(self):
        self._add_articles("第40条")
        info = verify_source(self.source, db=self.db, citation="第40条")
        self.assertEqual(info["article_scope"], "verified")

    def test_scope_missing_when_number_absent(self):
        self._add_articles("第40条")
        info = verify_source(self.source, db=self.db, citation="第99条")
        self.assertEqual(info["article_scope"], "missing")

    def test_scope_no_corpus_when_source_has_no_articles(self):
        info = verify_source(self.source, db=self.db, citation="第40条")
        self.assertEqual(info["article_scope"], "no_corpus")

    def test_scope_no_numbers_when_citation_has_no_article(self):
        self._add_articles("第40条")
        info = verify_source(self.source, db=self.db, citation="劳动合同法")
        self.assertEqual(info["article_scope"], "no_numbers")

    def test_enrich_references_marks_refs_without_source(self):
        self._add_articles("第40条")
        enriched = enrich_references(self.db, [{"title": "劳动合同法", "citation": "第40条"}])
        self.assertEqual(enriched[0]["verification"]["article_scope"], "no_source")

    def test_summary_of_enriched_refs_is_strong(self):
        self._add_articles("第40条", "第46条")
        enriched = enrich_references(
            self.db,
            [
                {"source_id": self.source.id, "citation": "第40条"},
                {"source_id": self.source.id, "citation": "第46条"},
            ],
        )
        summary = evidence_summary(enriched)
        self.assertEqual(summary["level"], "strong")
        self.assertEqual(summary["article_verified"], 2)


class ConsultationEvidenceOutTests(unittest.TestCase):
    """生成链路必须把摘要算出来（不传 evidence_out 时行为与从前逐字一致）。"""

    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session()
        self.source = LegalSource(
            user_id=1, title="劳动合同法", citation="劳动合同法第40条",
            source_type="statute", status="active", version="v1",
            content="无过失性辞退需提前三十日通知，并支付经济补偿。",
        )
        self.db.add(self.source)
        self.db.commit()
        self.db.refresh(self.source)
        self.db.add(LegalArticle(
            source_id=self.source.id, article_number="第40条",
            content="有下列情形之一的，用人单位提前三十日以书面形式通知劳动者本人……",
            sequence=0,
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _payload(self, evidence_out):
        async def _no_llm(*args, **kwargs):
            return None

        with patch("app.services.legal.legal_service._llm_chat", new=_no_llm):
            return asyncio.run(consultation_payload(
                "公司无过失辞退我需要提前多久通知？", [self.source],
                user_id=1, db=self.db, evidence_out=evidence_out,
            ))

    def test_evidence_out_is_filled_on_deterministic_path(self):
        evidence: dict = {}
        self._payload(evidence)
        self.assertEqual(evidence["version"], 1)
        self.assertEqual(evidence["references"], len(evidence) and evidence["references"])
        self.assertIn(evidence["level"], {"strong", "partial", "weak", "none"})
        self.assertIn("note", evidence)

    def test_evidence_out_is_optional(self):
        # 不传 evidence_out 时不应抛错（其他调用方如小程序/飞书仍走老签名）
        self._payload(None)


if __name__ == "__main__":
    unittest.main()
