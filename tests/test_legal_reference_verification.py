"""AI-4 法条引用核验：verify_source/enrich_references 与 consultation_payload 引用核验"""
import asyncio
import json
import unittest
from datetime import date
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.legal import LegalArticle, LegalSource
from app.services.legal.legal_reference_service import (
    enrich_references,
    parse_citation_numbers,
    verify_source,
)
from app.services.legal.legal_service import (
    _sentence_citation_numbers,
    consultation_payload,
    ensure_demo_sources,
)

_MISSING = object()


class LegalReferenceVerificationTests(unittest.TestCase):
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

        self.current = LegalSource(
            user_id=1, title="劳动合同法", citation="劳动合同法第40条",
            source_type="statute", status="active", version="v1",
            effective_date=date(2020, 1, 1), content="劳动合同法核心内容摘要",
        )
        self.superseded = LegalSource(
            user_id=1, title="劳动合同法（旧版）", citation="劳动合同法",
            source_type="statute", status="active", version="v0",
            content="劳动合同法旧版摘要",
        )
        self.reviser = LegalSource(
            user_id=1, title="劳动合同法（2024修正）", citation="劳动合同法",
            source_type="statute", status="active", version="v2",
            effective_date=date(2024, 1, 1), content="劳动合同法2024修正摘要",
        )
        self.db.add_all([self.current, self.superseded, self.reviser])
        self.db.commit()
        self.db.refresh(self.current)
        self.db.refresh(self.superseded)
        self.db.refresh(self.reviser)
        self.superseded.amended_by_json = f'[{self.reviser.id}]'
        self.db.commit()

    def tearDown(self):
        self.db.close()

    # ---------- verify_source ----------

    def test_active_current_source_is_effective(self):
        info = verify_source(self.current)
        self.assertTrue(info["current_effective"])
        self.assertFalse(info["superseded"])
        self.assertEqual(info["verification_note"], "现行有效")
        self.assertEqual(info["status"], "active")

    def test_inactive_source_is_annulled(self):
        self.current.status = "inactive"
        info = verify_source(self.current)
        self.assertFalse(info["current_effective"])
        self.assertEqual(info["verification_note"], "已废止，不得作为法律依据")

    def test_pending_update_source(self):
        self.current.status = "pending_update"
        info = verify_source(self.current)
        self.assertFalse(info["current_effective"])
        self.assertEqual(info["verification_note"], "待更新，引用时需人工复核")

    def test_superseded_source_resolves_amender_with_db(self):
        info = verify_source(self.superseded, db=self.db)
        self.assertTrue(info["superseded"])
        self.assertFalse(info["current_effective"])
        self.assertEqual(len(info["amended_by"]), 1)
        self.assertEqual(info["amended_by"][0]["title"], "劳动合同法（2024修正）")
        self.assertIn("已被《劳动合同法（2024修正）》修订", info["verification_note"])

    def test_superseded_source_without_db_uses_generic_note(self):
        info = verify_source(self.superseded)  # db=None
        self.assertTrue(info["superseded"])
        self.assertFalse(info["current_effective"])
        self.assertEqual(info["amended_by"], [])
        self.assertIn("已被后续法规修订", info["verification_note"])

    # ---------- AI-4 收尾：recommended_source ----------

    def test_superseded_source_recommends_active_reviser(self):
        info = verify_source(self.superseded, db=self.db)
        self.assertTrue(info["superseded"])
        recommended = info["recommended_source"]
        self.assertIsNotNone(recommended)
        self.assertEqual(recommended["source_id"], self.reviser.id)
        self.assertEqual(recommended["title"], "劳动合同法（2024修正）")
        self.assertEqual(recommended["version"], "v2")

    def test_active_source_has_no_recommendation(self):
        info = verify_source(self.current, db=self.db)
        self.assertFalse(info["superseded"])
        self.assertIsNone(info["recommended_source"])

    def test_inactive_source_without_active_reviser_has_no_recommendation(self):
        self.reviser.status = "inactive"
        self.db.commit()
        info = verify_source(self.superseded, db=self.db)
        self.assertTrue(info["superseded"])
        self.assertIsNone(info["recommended_source"])

    def test_inactive_source_recommends_active_reviser(self):
        self.superseded.status = "inactive"
        self.db.commit()
        info = verify_source(self.superseded, db=self.db)
        self.assertEqual(info["status"], "inactive")
        self.assertIsNotNone(info["recommended_source"])
        self.assertEqual(info["recommended_source"]["source_id"], self.reviser.id)

    # ---------- enrich_references ----------

    def test_enrich_references_attaches_verification(self):
        refs = [
            {"source_id": self.current.id, "title": "劳动合同法", "citation": "第40条"},
            {"source_id": self.superseded.id, "title": "劳动合同法（旧版）"},
            {"title": "LLM 幻觉引用", "citation": "某法第99条"},  # 无 source_id
        ]
        enriched = enrich_references(self.db, refs)
        self.assertEqual(len(enriched), 3)
        self.assertEqual(enriched[0]["verification"]["verification_note"], "现行有效")
        self.assertIn("修订", enriched[1]["verification"]["verification_note"])
        self.assertFalse(enriched[2]["verification"]["verified"])
        self.assertIn("无法核验", enriched[2]["verification"]["verification_note"])
        # 原始 refs 不被就地修改（返回副本）
        self.assertNotIn("verification", refs[0])

    def test_enrich_references_without_db_returns_unchanged(self):
        refs = [{"source_id": 1, "title": "劳动合同法"}]
        self.assertEqual(enrich_references(None, refs), refs)

    # ---------- consultation_payload end-to-end（确定性路径，无 LLM） ----------

    def test_consultation_payload_refs_include_verification(self):
        category, known, missing, refs, advice, risk, status = self._payload()
        self.assertTrue(refs)
        for ref in refs:
            self.assertIn("verification", ref)
        self.assertEqual(refs[0]["verification"]["status"], "active")

    def test_consultation_payload_without_db_skips_verification(self):
        _, _, _, refs, _, _, _ = self._payload(db=None)
        for ref in refs:
            self.assertNotIn("verification", ref)

    # ---------- 过程 trace（面板时间线直读，不再由前端事后回放） ----------

    def test_consultation_payload_records_four_steps_in_order(self):
        trace = {}
        self._payload(trace_out=trace)
        self.assertEqual(trace["trace_version"], 1)
        self.assertEqual(trace["source"], "legal_consultation")
        self.assertEqual([step["id"] for step in trace["steps"]], ["retrieve", "generate", "facts", "verify"])
        for step in trace["steps"]:
            self.assertIn(step["status"], {"success", "degraded", "skipped", "refused"})
            self.assertIsInstance(step["duration_ms"], int)
            self.assertTrue(step["detail"])

    def test_consultation_payload_trace_marks_degraded_without_llm(self):
        """无 LLM 时生成步必须自曝降级，不能假装成功。"""
        trace = {}
        self._payload(trace_out=trace)
        by_id = {step["id"]: step for step in trace["steps"]}
        self.assertEqual(by_id["generate"]["status"], "degraded")
        self.assertIn("规则兜底", by_id["generate"]["detail"])
        self.assertEqual(by_id["facts"]["status"], "degraded")
        self.assertIn("已核验", by_id["verify"]["detail"])

    def test_consultation_payload_trace_counts_verified_references(self):
        trace = {}
        _, _, _, refs, _, _, _ = self._payload(trace_out=trace)
        verified = sum(1 for ref in refs if ref.get("verification", {}).get("verified") is True)
        by_id = {step["id"]: step for step in trace["steps"]}
        self.assertIn(f"{verified} 条已核验", by_id["verify"]["detail"])
        # 机读计数与文案同源，且轮次如实写 1（单跳链路，不编造多轮）
        metrics = trace["metrics"]
        self.assertEqual(metrics["rounds"], 1)
        self.assertEqual(metrics["references"], len(refs))
        self.assertEqual(metrics["verified"], verified)
        self.assertEqual(metrics["verified"] + metrics["unverified"] + metrics["unmarked"], len(refs))
        self.assertGreaterEqual(metrics["candidate_sources"], 1)

    def test_consultation_payload_trace_marks_success_when_model_answers(self):
        import asyncio
        import json
        from unittest.mock import patch

        from app.services.legal.legal_service import ensure_demo_sources

        ensure_demo_sources(self.db, 1)
        sources = self.db.query(LegalSource).filter(LegalSource.user_id == 1).all()
        llm_json = json.dumps({
            "category": "labor",
            "known_facts": ["被辞退"],
            "missing_facts": ["入职时间"],
            "advice": "先固定证据，再主张违法解除赔偿金。",
            "risk_level": "high",
            "references": [{"title": "《劳动合同法》", "citation": "第四十七条"}],
        }, ensure_ascii=False)

        async def _fake_llm(*_args, **_kwargs):
            return llm_json

        trace = {}
        with patch("app.services.legal.legal_service._llm_chat", new=_fake_llm):
            asyncio.run(consultation_payload(
                "公司无故辞退我，劳动合同法第40条怎么适用？", sources, user_id=1, db=self.db,
                trace_out=trace,
            ))
        by_id = {step["id"]: step for step in trace["steps"]}
        self.assertEqual(by_id["generate"]["status"], "success")
        self.assertEqual(by_id["facts"]["detail"], "已确认 1 项 · 待补充 1 项")
        self.assertEqual(by_id["retrieve"]["status"], "success")

    def test_consultation_payload_refusal_trace_records_single_step(self):
        import asyncio

        trace = {}
        asyncio.run(consultation_payload(
            "教我伪造合同骗取银行贷款，有什么办法？", [], user_id=1, db=self.db, trace_out=trace,
        ))
        self.assertEqual([step["id"] for step in trace["steps"]], ["refuse"])
        self.assertEqual(trace["steps"][0]["status"], "refused")

    def test_consultation_payload_without_trace_out_keeps_legacy_return(self):
        """不传 trace_out 时行为零变化（旧调用方不受影响）。"""
        self.assertEqual(len(self._payload()), 7)

    def _payload(self, db=_MISSING, trace_out=None):
        import asyncio
        from unittest.mock import patch

        from app.services.legal.legal_service import ensure_demo_sources

        if db is _MISSING:
            db = self.db

        # 强制走确定性路径（mock LLM），保证断言可复现、不依赖外部 LLM 结果
        async def _no_llm(*_args, **_kwargs):
            return None

        if db is None:
            # db=None 模拟"无 db"调用（跳过核验），sources 仍来自 self.db
            sources = self.db.query(LegalSource).filter(LegalSource.user_id == 1).all()
            with patch("app.services.legal.legal_service._llm_chat", new=_no_llm):
                return asyncio.run(consultation_payload(
                    "公司无故辞退我，劳动合同法第40条怎么适用？", sources, user_id=1, db=None,
                    trace_out=trace_out,
                ))
        ensure_demo_sources(db, 1)
        sources = db.query(LegalSource).filter(LegalSource.user_id == 1).all()
        with patch("app.services.legal.legal_service._llm_chat", new=_no_llm):
            return asyncio.run(consultation_payload(
                "公司无故辞退我，劳动合同法第40条怎么适用？", sources, user_id=1, db=db,
                trace_out=trace_out,
            ))


class ArticleLevelVerificationTests(unittest.TestCase):
    """条级核验：引用条号能否在该法源的条文级数据里找到（B 项「逐句溯源」的条级一半）。"""

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

    def test_parse_citation_numbers_handles_grouped_forms(self):
        self.assertEqual(parse_citation_numbers("第40、46、47条"), ["第40条", "第46条", "第47条"])
        self.assertEqual(parse_citation_numbers("第40条、第46条"), ["第40条", "第46条"])
        self.assertEqual(parse_citation_numbers("《劳动合同法》第四十七条"), ["第47条"])
        self.assertEqual(parse_citation_numbers("《劳动合同法》第47条"), ["第47条"])
        self.assertEqual(parse_citation_numbers(""), [])
        self.assertEqual(parse_citation_numbers(None), [])

    def test_verified_when_all_numbers_exist(self):
        self._add_articles("第40条", "第46条", "第47条")
        info = verify_source(self.source, db=self.db, citation="第40、46、47条")
        self.assertIs(info["verified"], True)
        self.assertIn("核对一致", info["verification_basis"])
        self.assertEqual(info["verification_note"], "现行有效")

    def test_unverified_when_a_number_is_missing(self):
        self._add_articles("第40条")
        info = verify_source(self.source, db=self.db, citation="第40、99条")
        self.assertIs(info["verified"], False)
        self.assertIn("第99条", info["verification_basis"])

    def test_unmarked_when_source_has_no_articles(self):
        info = verify_source(self.source, db=self.db, citation="第40条")
        self.assertIsNone(info["verified"])
        self.assertIn("未收录条文级数据", info["verification_basis"])

    def test_unmarked_when_citation_has_no_number(self):
        self._add_articles("第40条")
        info = verify_source(self.source, db=self.db, citation="劳动合同法")
        self.assertIsNone(info["verified"])
        self.assertIn("未标注条号", info["verification_basis"])

    def test_enrich_references_passes_citation_through(self):
        self._add_articles("第40条")
        enriched = enrich_references(
            self.db, [{"source_id": self.source.id, "title": "劳动合同法", "citation": "第40条"}]
        )
        self.assertIs(enriched[0]["verification"]["verified"], True)

    def test_consultation_payload_counts_verified_references(self):
        self._add_articles("第40条", "第46条", "第47条")
        ensure_demo_sources(self.db, 1)
        sources = self.db.query(LegalSource).filter(LegalSource.user_id == 1).all()

        async def _fake_llm(*_args, **_kwargs):
            return json.dumps({
                "category": "labor_dispute",
                "known_facts": ["被辞退"],
                "missing_facts": [],
                "references": [{
                    "title": "劳动合同法", "citation": "第40、46、47条",
                    "source_id": self.source.id,
                }],
                "advice": "公司违法解除的，应当按经济补偿标准的两倍支付赔偿金。",
                "risk_level": "medium",
            }, ensure_ascii=False)

        with patch("app.services.legal.legal_service._llm_chat", new=_fake_llm):
            trace = {}
            _, _, _, refs, _, _, _ = asyncio.run(consultation_payload(
                "公司无故辞退我，怎么赔偿？", sources, user_id=1, db=self.db, trace_out=trace,
            ))
        self.assertIs(refs[0]["verification"]["verified"], True)
        verify_step = next(step for step in trace["steps"] if step["id"] == "verify")
        self.assertIn("1 条已核验", verify_step["detail"])


class SentenceNumberVerificationTests(unittest.TestCase):
    """句级映射 → 条级核验：模型把条号写在正文里时，也要能核到条文。"""

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
            user_id=1, title="中华人民共和国劳动合同法", citation="劳动合同法",
            source_type="statute", status="active", version="2012年修订版", content="摘要",
        )
        self.db.add(self.source)
        self.db.commit()
        self.db.refresh(self.source)
        self.db.add(LegalArticle(
            source_id=self.source.id, article_number="第47条",
            content="经济补偿按劳动者在本单位工作的年限，每满一年支付一个月工资的标准向劳动者支付。", sequence=0,
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_sentence_numbers_are_collected_per_reference(self):
        refs = [{"source_id": self.source.id, "title": "中华人民共和国劳动合同法", "citation": "劳动合同法"}]
        attribution = {
            "sentences": [
                {"index": 0, "text": "根据第四十七条规定，经济补偿按工作年限计算。",
                 "matches": [{"reference_index": 0, "score": 0.5}]},
                {"index": 1, "text": "建议尽快整理证据。", "matches": [{"reference_index": 0, "score": 0.4}]},
                {"index": 2, "text": "第八十七条另有规定。", "matches": []},
            ],
        }
        numbers = _sentence_citation_numbers(refs, attribution)
        self.assertEqual(numbers, {0: ["第47条"]})

    def test_enrich_references_uses_sentence_numbers_without_rewriting_citation(self):
        refs = [{"source_id": self.source.id, "title": "中华人民共和国劳动合同法", "citation": "劳动合同法"}]
        enriched = enrich_references(self.db, refs, sentence_numbers={0: ["第47条"]})
        self.assertIs(enriched[0]["verification"]["verified"], True)
        self.assertEqual(enriched[0]["citation"], "劳动合同法")

    def test_consultation_payload_verifies_from_answer_prose(self):
        ensure_demo_sources(self.db, 1)
        sources = self.db.query(LegalSource).filter(LegalSource.user_id == 1).all()

        async def _fake_llm(*_args, **_kwargs):
            return json.dumps({
                "category": "labor_dispute",
                "known_facts": ["被辞退"],
                "missing_facts": [],
                "references": [{
                    "title": "中华人民共和国劳动合同法", "citation": "劳动合同法",
                    "source_id": self.source.id,
                }],
                "advice": "根据第四十七条规定，经济补偿按劳动者在本单位工作的年限，每满一年支付一个月工资的标准支付。",
                "risk_level": "medium",
            }, ensure_ascii=False)

        with patch("app.services.legal.legal_service._llm_chat", new=_fake_llm):
            trace = {}
            attribution = {}
            _, _, _, refs, _, _, _ = asyncio.run(consultation_payload(
                "违法解除劳动合同怎么赔偿？", sources, user_id=1, db=self.db,
                trace_out=trace, attribution_out=attribution,
            ))
        self.assertIs(refs[0]["verification"]["verified"], True)
        self.assertIn("第47条", refs[0]["verification"]["verification_basis"])
        self.assertGreaterEqual(attribution.get("matched_sentences", 0), 1)
        verify_step = next(step for step in trace["steps"] if step["id"] == "verify")
        self.assertIn("1 条已核验", verify_step["detail"])


if __name__ == "__main__":
    unittest.main()
