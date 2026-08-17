"""AI-4 法条引用核验：verify_source/enrich_references 与 consultation_payload 引用核验"""
import unittest
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.legal import LegalSource
from app.services.legal.legal_reference_service import enrich_references, verify_source
from app.services.legal.legal_service import consultation_payload

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

    def _payload(self, db=_MISSING):
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
                ))
        ensure_demo_sources(db, 1)
        sources = db.query(LegalSource).filter(LegalSource.user_id == 1).all()
        with patch("app.services.legal.legal_service._llm_chat", new=_no_llm):
            return asyncio.run(consultation_payload(
                "公司无故辞退我，劳动合同法第40条怎么适用？", sources, user_id=1, db=db,
            ))


if __name__ == "__main__":
    unittest.main()
