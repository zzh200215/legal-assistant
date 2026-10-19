"""历史咨询的证据可核验性回填：只补 `evidence_json`，不碰历史核验快照。

守三件事：
1. 回填走与生成时同一条路径（句级条号 + 按标题补 source_id + `enrich_references`）；
2. 写回的摘要必须带 `basis="recomputed"`——它回答「以当前法源库看能不能核到」，
   不是生成当时的结论，聚合与界面据此区分；
3. 没有引用的老记录不硬塞摘要（`references_json` 为 `[]` 时返回 None）。
"""

import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.legal import LegalArticle, LegalConsultation, LegalSource
from scripts.backfill_consultation_evidence import BASIS_RECOMPUTED, build_evidence, has_material

NOW = "2026-10-19T00:00:00+00:00"


class BackfillConsultationEvidenceTests(unittest.TestCase):
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
            user_id=10,
            title="《中华人民共和国劳动合同法》",
            citation="劳动合同法",
            source_type="statute",
            status="active",
            version="2012年修订版",
            content="劳动合同法全文。",
        )
        self.db.add(self.source)
        self.db.commit()
        self.db.refresh(self.source)
        self._add_articles("第40条", "第46条", "第47条")

    def tearDown(self):
        self.db.close()

    def _add_articles(self, *numbers: str) -> None:
        for index, number in enumerate(numbers):
            self.db.add(
                LegalArticle(
                    source_id=self.source.id,
                    article_number=number,
                    content=f"{number}的条文正文。",
                    sequence=index,
                )
            )
        self.db.commit()

    def _consultation(self, *, references, trace=None) -> LegalConsultation:
        row = LegalConsultation(
            user_id=10,
            question="公司解除劳动合同要提前多久通知？",
            category="labor",
            advice="需提前三十日通知或额外支付一个月工资。",
            risk_level="medium",
            status="pending_review",
            references_json=json.dumps(references, ensure_ascii=False),
            trace_json=json.dumps(trace, ensure_ascii=False) if trace is not None else None,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def test_backfill_fills_source_id_by_title_and_marks_basis(self):
        row = self._consultation(
            references=[{"title": "《中华人民共和国劳动合同法》", "citation": "劳动合同法第40、46、47条"}]
        )

        summary = build_evidence(self.db, row, sources=[self.source], now=NOW)

        self.assertEqual(summary["level"], "strong")
        # 计数口径是「引用条数」不是「条号个数」：这条引用里的三个条号都核到了 → 1 条引用通过
        self.assertEqual(summary["references"], 1)
        self.assertEqual(summary["article_verified"], 1)
        self.assertIn("核对一致", summary["note"])
        self.assertEqual(summary["basis"], BASIS_RECOMPUTED)
        self.assertEqual(summary["computed_at"], NOW)

    def test_material_context_is_counted_from_trace(self):
        with_material = self._consultation(
            references=[{"source_id": self.source.id, "citation": "第40条"}],
            trace={"contexts": [{"type": "contract", "id": 1, "title": "技术服务合同", "chars": 120}]},
        )
        without = self._consultation(references=[{"source_id": self.source.id, "citation": "第40条"}])

        self.assertTrue(has_material(with_material.trace_json))
        self.assertFalse(has_material(without.trace_json))
        self.assertEqual(build_evidence(self.db, with_material, sources=[self.source], now=NOW)["materials"], 1)
        self.assertEqual(build_evidence(self.db, without, sources=[self.source], now=NOW)["materials"], 0)

    def test_missing_article_number_is_weak(self):
        row = self._consultation(references=[{"source_id": self.source.id, "citation": "第99条"}])

        summary = build_evidence(self.db, row, sources=[self.source], now=NOW)

        self.assertEqual(summary["level"], "weak")
        self.assertEqual(summary["article_missing"], 1)

    def test_source_without_articles_is_no_corpus(self):
        empty = LegalSource(
            user_id=10, title="服务合同审查要点摘要（演示模板）", citation="模板",
            source_type="template", status="active", version="v1", content="模板。",
        )
        self.db.add(empty)
        self.db.commit()
        self.db.refresh(empty)
        row = self._consultation(references=[{"source_id": empty.id, "citation": "第1条"}])

        summary = build_evidence(self.db, row, sources=[self.source, empty], now=NOW)

        self.assertEqual(summary["no_corpus"], 1)
        self.assertEqual(summary["level"], "weak")

    def test_row_without_references_returns_none(self):
        row = self._consultation(references=[])

        self.assertIsNone(build_evidence(self.db, row, sources=[self.source], now=NOW))


if __name__ == "__main__":
    unittest.main()
