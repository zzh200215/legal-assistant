"""证据可核验性在线指标的聚合口径（管理员观测 tab 的数据源）。

守两件事：
1. 四档标签与 `legal_reference_service._EVIDENCE_LEVELS` **同源**（漂移会被这条断言拦住）；
2. 「没统计过」不等于「核验失败」——没有 `evidence_json` 的老记录单独计数，不摊进比率分母。
"""

import json
import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.legal import LegalArticle, LegalConsultation, LegalSource
from app.models.user import User
from app.services.legal import legal_reference_service
from app.services.observability.analytics_service import analytics_service
from app.services.observability.consultation_evidence import LEVEL_LABELS


def _evidence(level: str, **counts) -> str:
    payload = {
        "version": 1,
        "level": level,
        "label": LEVEL_LABELS[level],
        "references": counts.get("references", 0),
        "article_checked": counts.get("article_checked", 0),
        "article_verified": counts.get("article_verified", 0),
        "article_missing": counts.get("article_missing", 0),
        "no_corpus": counts.get("no_corpus", 0),
        "no_numbers": counts.get("no_numbers", 0),
        "unmatched": counts.get("unmatched", 0),
        "materials": counts.get("materials", 0),
        "note": "",
    }
    return json.dumps(payload, ensure_ascii=False)


class ConsultationEvidenceStatsTests(unittest.TestCase):
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
        self.now = datetime.utcnow()

    def tearDown(self):
        self.db.close()

    def _consultation(
        self,
        *,
        user_id: int = 1,
        evidence: str | None = None,
        references: list[dict] | None = None,
        days_ago: int = 0,
    ) -> LegalConsultation:
        row = LegalConsultation(
            user_id=user_id,
            question="试用期最长可以约定多久？",
            category="labor",
            advice="三年以上固定期限劳动合同试用期不得超过六个月。",
            risk_level="medium",
            status="pending_review",
            evidence_json=evidence,
            references_json=json.dumps(references or [], ensure_ascii=False),
            created_at=self.now - timedelta(days=days_ago),
        )
        self.db.add(row)
        self.db.commit()
        return row

    def _source(self, *, source_id: int, title: str, articles: int = 0) -> None:
        self.db.add(
            LegalSource(
                id=source_id,
                user_id=1,
                title=title,
                source_type="statute",
                citation=title,
                content=f"{title} 摘要",
                version="v1",
            )
        )
        for index in range(articles):
            self.db.add(
                LegalArticle(
                    source_id=source_id,
                    article_number=f"第{index + 1}条",
                    content=f"条文正文 {index + 1}",
                    sequence=index + 1,
                )
            )
        self.db.commit()

    def test_level_labels_match_reference_service(self):
        # 两处标签必须逐字一致：前端徽标与观测 tab 说的得是同一件事
        self.assertEqual(LEVEL_LABELS, legal_reference_service._EVIDENCE_LEVELS)

    def test_distribution_and_rates(self):
        self._consultation(evidence=_evidence("strong", references=2, article_checked=2, article_verified=2))
        self._consultation(evidence=_evidence("partial", references=2, article_checked=1, article_verified=1, no_corpus=1))
        self._consultation(evidence=_evidence("weak", references=1, article_checked=1, article_missing=1))
        self._consultation(evidence=_evidence("none"))

        stats = analytics_service.get_consultation_evidence_stats(self.db, days=30)

        self.assertEqual(stats["total_consultations"], 4)
        self.assertEqual(stats["with_evidence"], 4)
        self.assertEqual(stats["without_evidence"], 0)
        self.assertEqual(stats["evidence_coverage"], 1.0)
        by_level = {row["level"]: row for row in stats["levels"]}
        self.assertEqual(by_level["strong"]["count"], 1)
        self.assertEqual(by_level["strong"]["share"], 0.25)
        self.assertEqual(by_level["none"]["label"], "未引用法源")
        self.assertEqual(stats["references"]["references"], 5)
        self.assertEqual(stats["references"]["article_checked"], 4)
        self.assertEqual(stats["references"]["article_verified"], 3)
        # 通过率只算真的做过条文级核对的引用：3 / 4
        self.assertEqual(stats["checked_rate"], 0.75)

    def test_rows_without_evidence_stay_out_of_denominators(self):
        self._consultation(evidence=_evidence("strong", references=1, article_checked=1, article_verified=1))
        self._consultation(evidence=None)
        self._consultation(evidence="not-json")

        stats = analytics_service.get_consultation_evidence_stats(self.db, days=30)

        self.assertEqual(stats["total_consultations"], 3)
        self.assertEqual(stats["with_evidence"], 1)
        self.assertEqual(stats["without_evidence"], 2)
        self.assertEqual(stats["evidence_coverage"], round(1 / 3, 4))
        self.assertEqual({row["level"]: row["share"] for row in stats["levels"]}["strong"], 1.0)
        self.assertEqual(stats["checked_rate"], 1.0)

    def test_window_excludes_older_consultations(self):
        self._consultation(evidence=_evidence("strong", references=1, article_checked=1, article_verified=1))
        self._consultation(evidence=_evidence("weak", references=1, article_checked=1), days_ago=40)

        recent = analytics_service.get_consultation_evidence_stats(self.db, days=7)
        wider = analytics_service.get_consultation_evidence_stats(self.db, days=90)

        self.assertEqual(recent["total_consultations"], 1)
        self.assertEqual(recent["window_days"], 7)
        self.assertEqual(wider["total_consultations"], 2)

    def test_organization_filter_scopes_to_members(self):
        self.db.add(User(id=7, username="u7", email="u7@example.com", hashed_password="x", organization_id=15))
        self.db.add(User(id=8, username="u8", email="u8@example.com", hashed_password="x", organization_id=16))
        self.db.commit()
        self._consultation(user_id=7, evidence=_evidence("strong", references=1, article_checked=1, article_verified=1))
        self._consultation(user_id=8, evidence=_evidence("weak", references=1, article_checked=1))

        scoped = analytics_service.get_consultation_evidence_stats(self.db, days=30, organization_id=15)
        unscoped = analytics_service.get_consultation_evidence_stats(self.db, days=30)

        self.assertEqual(scoped["total_consultations"], 1)
        self.assertEqual(scoped["levels"][0]["level"], "strong")
        self.assertEqual(unscoped["total_consultations"], 2)

    def test_empty_window_reports_none_rates(self):
        stats = analytics_service.get_consultation_evidence_stats(self.db, days=30)

        self.assertEqual(stats["total_consultations"], 0)
        # 没有样本时返回 None（不是 0%），前端显示「-」
        self.assertIsNone(stats["checked_rate"])
        self.assertIsNone(stats["evidence_coverage"])
        self.assertEqual([row["count"] for row in stats["levels"]], [0, 0, 0, 0])
        self.assertEqual(stats["trend"], [])
        self.assertEqual(stats["sources"], [])
        self.assertEqual(stats["corpus_gaps"], [])
        self.assertEqual(stats["unmatched_references"], {"references": 0, "consultations": 0})

    def test_trend_buckets_by_day(self):
        self._consultation(evidence=_evidence("strong", references=2, article_checked=2, article_verified=2))
        self._consultation(evidence=_evidence("partial", references=2, article_checked=1, article_verified=1))
        self._consultation(evidence=_evidence("weak", references=1, article_checked=1), days_ago=3)
        self._consultation(evidence=None, days_ago=3)

        stats = analytics_service.get_consultation_evidence_stats(self.db, days=30)
        trend = stats["trend"]

        # 只有有数据的日期出现，按日期升序
        self.assertEqual([row["date"] for row in trend], sorted(row["date"] for row in trend))
        self.assertEqual(len(trend), 2)
        today = trend[-1]
        self.assertEqual(today["total"], 2)
        self.assertEqual(today["with_evidence"], 2)
        self.assertEqual(today["strong"], 1)
        self.assertEqual(today["partial"], 1)
        self.assertEqual(today["article_checked"], 3)
        self.assertEqual(today["article_verified"], 3)
        older = trend[0]
        self.assertEqual(older["total"], 2)  # 含一条没有 evidence 的老记录
        self.assertEqual(older["with_evidence"], 1)
        self.assertEqual(older["weak"], 1)

    def test_source_ranking_and_corpus_gaps(self):
        self._source(source_id=1, title="《中华人民共和国劳动合同法》", articles=2)
        self._source(source_id=2, title="服务合同审查要点摘要（演示模板）", articles=0)
        verified = {"verified": True, "article_scope": "verified"}
        no_corpus = {"verified": False, "article_scope": "no_corpus"}
        self._consultation(
            evidence=_evidence("partial", references=3, article_checked=2, article_verified=1, no_corpus=1),
            references=[
                {"source_id": 1, "citation": "劳动合同法第40条", "verification": verified},
                {"source_id": 1, "citation": "劳动合同法第47条", "verification": verified},
                {"source_id": 2, "citation": "服务合同审查要点", "verification": no_corpus},
            ],
        )
        self._consultation(
            evidence=_evidence("weak", references=1, article_checked=1),
            references=[
                {"title": "《中华人民共和国劳动争议调解仲裁法》", "citation": "第27条"},  # 没挂 source_id
                {"source_id": 1, "citation": "劳动合同法第87条", "verification": {"verified": False, "article_scope": "missing"}},
            ],
        )

        stats = analytics_service.get_consultation_evidence_stats(self.db, days=30)
        sources = {row["source_id"]: row for row in stats["sources"]}

        self.assertEqual(stats["sources_total"], 2)
        # 按引用次数倒序：法源 1 被引 3 次、法源 2 被引 1 次
        self.assertEqual([row["source_id"] for row in stats["sources"]], [1, 2])
        self.assertEqual(sources[1]["references"], 3)
        self.assertEqual(sources[1]["consultations"], 2)
        self.assertEqual(sources[1]["verified"], 2)
        self.assertEqual(sources[1]["articles"], 2)
        self.assertEqual(sources[2]["no_corpus"], 1)
        self.assertEqual(sources[2]["articles"], 0)
        # 缺口清单只含没有条文级数据的法源
        self.assertEqual([row["source_id"] for row in stats["corpus_gaps"]], [2])
        # 没挂 source_id 的引用单独计数，不进排行
        self.assertEqual(stats["unmatched_references"], {"references": 1, "consultations": 1})

    def test_source_row_survives_deleted_source(self):
        self._consultation(
            evidence=_evidence("weak", references=1, article_checked=1),
            references=[{"source_id": 999, "citation": "已删除法源第1条"}],
        )

        stats = analytics_service.get_consultation_evidence_stats(self.db, days=30)

        self.assertEqual(stats["sources"][0]["source_id"], 999)
        self.assertEqual(stats["sources"][0]["title"], "法源 #999（已删除）")
        self.assertEqual(stats["sources"][0]["articles"], 0)
        self.assertEqual([row["source_id"] for row in stats["corpus_gaps"]], [999])


if __name__ == "__main__":
    unittest.main()
