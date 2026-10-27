"""引用质量问题清单：从聚合数字下钻到具体答复（step51）。

守三件事：
1. 问题分类只认 `references_json.verification.article_scope` 与 `evidence_json.level`；
2. 没有 `evidence_json` 的老记录不进来（与统计口径一致）；
3. 按法源下钻时允许看到该法源的全部引用（含已核对），按问题类型下钻只留对应问题。
"""

import json
import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import hash_password
from app.core.database import Base
from app.models.legal import LegalConsultation, LegalSource
from app.models.org import Organization
from app.models.user import User
from app.services.observability.analytics_service import analytics_service
from app.services.observability.consultation_evidence import LEVEL_LABELS


def _evidence(level: str, **counts) -> str:
    return json.dumps(
        {
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
            "materials": 0,
            "note": "",
        },
        ensure_ascii=False,
    )


def _ref(source_id, scope, *, title="劳动合同法", citation="劳动合同法第19条", note="核对说明"):
    payload = {"title": title, "citation": citation}
    if source_id is not None:
        payload["source_id"] = source_id
    payload["verification"] = {
        "article_scope": scope,
        "verified": True if scope == "verified" else None,
        "verification_note": note,
    }
    return payload


class ConsultationEvidenceIssuesTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)()
        self.now = datetime.utcnow()

        self.org = Organization(name="证据所", code="EVID")
        self.other_org = Organization(name="另一个所", code="EVID2")
        self.db.add_all([self.org, self.other_org])
        self.db.flush()
        self.user = User(
            username="evidence_user", email="evidence@example.com",
            hashed_password=hash_password("pw"), organization_id=self.org.id,
        )
        self.other_user = User(
            username="other_user", email="other@example.com",
            hashed_password=hash_password("pw"), organization_id=self.other_org.id,
        )
        self.db.add_all([self.user, self.other_user])
        self.db.flush()
        self.db.add(LegalSource(
            id=10, user_id=self.user.id, title="劳动合同法", source_type="statute",
            citation="劳动合同法", content="摘要", version="2012",
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _consultation(
        self,
        *,
        user_id: int | None = None,
        evidence: str | None = None,
        refs: list[dict] | None = None,
        question: str = "试用期可以约定多久？",
        days_ago: int = 0,
    ) -> LegalConsultation:
        row = LegalConsultation(
            user_id=user_id or self.user.id,
            question=question,
            category="labor",
            advice="答复正文。",
            risk_level="medium",
            status="pending_review",
            evidence_json=evidence,
            references_json=json.dumps(refs or [], ensure_ascii=False),
            created_at=self.now - timedelta(days=days_ago),
        )
        self.db.add(row)
        self.db.commit()
        return row

    def _issues(self, **kwargs):
        return analytics_service.list_consultation_evidence_issues(self.db, days=30, **kwargs)

    def test_lists_issue_types_and_excludes_strong_answers(self):
        no_corpus = self._consultation(
            evidence=_evidence("weak", references=1, no_corpus=1),
            refs=[_ref(10, "no_corpus", note="该法源没有条文级数据")],
            question="缺条文的问题",
        )
        missing = self._consultation(
            evidence=_evidence("weak", references=1, article_checked=1, article_missing=1),
            refs=[_ref(10, "missing", note="未在已收录条文中找到第99条")],
            question="条号找不到的问题",
        )
        unmatched = self._consultation(
            evidence=_evidence("partial", references=1, unmatched=1),
            refs=[_ref(None, "no_source", title="未挂法源的引用", citation="某法规")],
            question="没挂法源的问题",
        )
        weak_only = self._consultation(
            evidence=_evidence("weak"), refs=[], question="没有引用的待核答复",
        )
        self._consultation(
            evidence=_evidence("strong", references=1, article_checked=1, article_verified=1),
            refs=[_ref(10, "verified")],
            question="已核对的问题",
        )

        data = self._issues()
        self.assertEqual(data["total"], 4)
        self.assertEqual(
            [row["id"] for row in data["items"]],
            [weak_only.id, unmatched.id, missing.id, no_corpus.id],
        )
        by_id = {row["id"]: row for row in data["items"]}
        self.assertEqual(by_id[no_corpus.id]["issue_labels"], ["法源缺条文", "答复待核"])
        self.assertEqual(by_id[missing.id]["issue_labels"], ["条号未找到", "答复待核"])
        self.assertEqual(by_id[unmatched.id]["issue_labels"], ["未挂法源"])
        self.assertEqual(by_id[weak_only.id]["issue_labels"], ["答复待核"])
        self.assertEqual(by_id[no_corpus.id]["references"][0]["note"], "该法源没有条文级数据")

    def test_filters_by_issue_and_source(self):
        no_corpus = self._consultation(
            evidence=_evidence("weak", references=1, no_corpus=1),
            refs=[_ref(10, "no_corpus")],
        )
        missing = self._consultation(
            evidence=_evidence("weak", references=1, article_checked=1, article_missing=1),
            refs=[_ref(10, "missing")],
        )
        verified = self._consultation(
            evidence=_evidence("strong", references=1, article_checked=1, article_verified=1),
            refs=[_ref(10, "verified")],
        )
        deleted = self._consultation(
            evidence=_evidence("weak", references=1, no_corpus=1),
            refs=[_ref(99, "no_corpus", title="已删除的法源")],
        )

        by_no_corpus = self._issues(issue="no_corpus")
        self.assertEqual({row["id"] for row in by_no_corpus["items"]}, {no_corpus.id, deleted.id})
        by_missing = self._issues(issue="missing")
        self.assertEqual([row["id"] for row in by_missing["items"]], [missing.id])
        by_weak = self._issues(issue="weak")
        self.assertEqual(len(by_weak["items"]), 3)  # 三条都是 weak（含已删除法源那条）

        # 按法源下钻：包含已核对答复，只保留该法源的引用明细
        by_source = self._issues(source_id=10)
        self.assertEqual({row["id"] for row in by_source["items"]}, {no_corpus.id, missing.id, verified.id})
        for row in by_source["items"]:
            self.assertEqual({ref["source_id"] for ref in row["references"]}, {10})

        source_and_issue = self._issues(source_id=10, issue="missing")
        self.assertEqual([row["id"] for row in source_and_issue["items"]], [missing.id])

    def test_organization_filter_and_limit(self):
        for index in range(3):
            self._consultation(
                evidence=_evidence("weak", references=1, no_corpus=1),
                refs=[_ref(10, "no_corpus")],
                question=f"问题 {index}",
            )
        self._consultation(
            user_id=self.other_user.id,
            evidence=_evidence("weak", references=1, no_corpus=1),
            refs=[_ref(10, "no_corpus")],
            question="别组织的问题",
        )

        own = self._issues(organization_id=self.org.id)
        self.assertEqual(own["total"], 3)
        limited = self._issues(organization_id=self.org.id, limit=1)
        self.assertEqual(limited["total"], 3)
        self.assertEqual(limited["returned"], 1)
        self.assertEqual(len(limited["items"]), 1)

    def test_old_record_without_evidence_is_skipped_and_bad_issue_rejected(self):
        self._consultation(evidence=None, refs=[_ref(10, "no_corpus")])
        self.assertEqual(self._issues()["total"], 0)
        with self.assertRaisesRegex(ValueError, "未知的引用问题类型"):
            self._issues(issue="bad")


if __name__ == "__main__":
    unittest.main()
