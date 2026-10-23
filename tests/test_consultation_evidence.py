"""咨询侧证据链摘要：引用/句级溯源/忠实度 → 等级（读时派生，不写库）。"""

import json
import unittest

from app.models.legal import LegalConsultation
from app.services.legal.consultation_evidence import (
    compare_consultation_evidence,
    summarize_consultation_evidence,
)
from app.services.legal.legal_workspace_service import serialize_workspace_row


def _consultation(**overrides):
    row = LegalConsultation(
        user_id=1, question="公司辞退我是否要补偿？", category="labor",
        known_facts_json="[]", missing_facts_json="[]", references_json="[]",
        advice="可以主张经济补偿。", risk_level="medium", status="draft",
    )
    for key, value in overrides.items():
        setattr(row, key, json.dumps(value, ensure_ascii=False))
    return row


def _attribution(total, matched):
    return {
        "granularity": "sentence",
        "total_sentences": total,
        "matched_sentences": matched,
        "sentences": [],
    }


class SummarizeConsultationEvidenceTests(unittest.TestCase):
    def test_no_references_is_none(self):
        summary = summarize_consultation_evidence(_consultation())
        self.assertEqual(summary["references"], 0)
        self.assertEqual(summary["level"], "none")
        self.assertEqual(summary["faithfulness"], "unchecked")

    def test_strong_when_coverage_high_and_refs_active(self):
        row = _consultation(
            references_json=[{"title": "劳动合同法", "status": "active"}],
            attribution_json=_attribution(5, 4),
            faithfulness_json={"available": True, "faithful": True, "reason": "ok"},
        )
        summary = summarize_consultation_evidence(row)
        self.assertEqual(summary["sentences"], 5)
        self.assertEqual(summary["attributed"], 4)
        self.assertEqual(summary["attributed_rate"], 0.8)
        self.assertEqual(summary["faithfulness"], "pass")
        self.assertEqual(summary["level"], "strong")

    def test_partial_when_coverage_between_half_and_strong(self):
        row = _consultation(
            references_json=[{"title": "劳动合同法", "status": "active"}],
            attribution_json=_attribution(10, 6),
        )
        self.assertEqual(summarize_consultation_evidence(row)["level"], "partial")

    def test_fail_faithfulness_forces_weak(self):
        row = _consultation(
            references_json=[{"title": "劳动合同法", "status": "active"}],
            attribution_json=_attribution(5, 5),
            faithfulness_json={"available": True, "faithful": False, "unsupported": ["一句无依据"]},
        )
        summary = summarize_consultation_evidence(row)
        self.assertEqual(summary["faithfulness"], "fail")
        self.assertEqual(summary["unsupported"], 1)
        self.assertEqual(summary["level"], "weak")

    def test_legacy_without_attribution_is_partial_not_strong(self):
        row = _consultation(references_json=[{"title": "劳动合同法", "status": "active"}])
        summary = summarize_consultation_evidence(row)
        self.assertEqual(summary["sentences"], 0)
        self.assertIsNone(summary["attributed_rate"])
        self.assertEqual(summary["level"], "partial")

    def test_inactive_references_cannot_be_strong(self):
        row = _consultation(
            references_json=[{"title": "已废止法", "status": "inactive"}],
            attribution_json=_attribution(3, 3),
        )
        self.assertEqual(summarize_consultation_evidence(row)["level"], "partial")

    def test_serializer_attaches_summary(self):
        row = _consultation(
            references_json=[{"title": "劳动合同法", "status": "active"}],
            attribution_json=_attribution(4, 4),
        )
        payload = serialize_workspace_row(row)
        self.assertIn("evidence_summary", payload)
        self.assertEqual(payload["evidence_summary"]["level"], "strong")
        self.assertEqual(payload["evidence_summary"]["references"], 1)


class ListConsultationsEvidenceFilterTests(unittest.TestCase):
    """列表接口按证据链等级筛选：无引用咨询可单独列出。"""

    def setUp(self):
        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        import app.models  # noqa: F401
        from app.core.auth import create_access_token, hash_password
        from app.core.database import Base, get_db
        from app.main import app
        from app.models.user import User

        engine = create_engine(
            "sqlite+pysqlite:///:memory:", future=True,
            connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        db = self.Session()
        self.user = User(username="chain_filter", email="chain_filter@example.com",
                         hashed_password=hash_password("secret"), role="user")
        db.add(self.user)
        db.flush()
        self.user_id = self.user.id
        db.add_all([
            LegalConsultation(
                user_id=self.user_id, question="有引用的咨询", category="labor",
                known_facts_json="[]", missing_facts_json="[]",
                references_json=json.dumps([{"title": "劳动合同法", "status": "active"}], ensure_ascii=False),
                advice="应支付补偿。", risk_level="medium", status="draft",
                attribution_json=json.dumps({"total_sentences": 4, "matched_sentences": 4, "sentences": []}),
            ),
            LegalConsultation(
                user_id=self.user_id, question="没有引用的咨询", category="labor",
                known_facts_json="[]", missing_facts_json="[]", references_json="[]",
                advice="建议咨询律师。", risk_level="low", status="draft",
            ),
        ])
        db.commit()
        db.close()

        def override_get_db():
            session = self.Session()
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)
        self.headers = {"Authorization": f"Bearer {create_access_token({'sub': self.user_id})}"}

    def tearDown(self):
        from app.core.database import get_db
        from app.main import app

        app.dependency_overrides.clear()

    def test_filter_none_returns_only_uncited(self):
        response = self.client.get("/api/legal/consultations?evidence_level=none", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        rows = response.json()["data"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["question"], "没有引用的咨询")
        self.assertEqual(rows[0]["evidence_summary"]["level"], "none")

    def test_filter_strong_returns_cited(self):
        response = self.client.get("/api/legal/consultations?evidence_level=strong", headers=self.headers)
        self.assertEqual(len(response.json()["data"]), 1)
        self.assertEqual(response.json()["data"][0]["question"], "有引用的咨询")

    def test_followup_detail_includes_comparison(self):
        """追问记录带 parent_id，详情接口返回补充前后引用对比。"""
        db = self.Session()
        parent = LegalConsultation(
            user_id=self.user_id, question="原咨询", category="labor",
            known_facts_json="[]", missing_facts_json="[]",
            references_json=json.dumps([{"title": "劳动合同法", "source_id": 1, "status": "active"}], ensure_ascii=False),
            advice="原答复", risk_level="low", status="draft",
        )
        parent.attribution_json = json.dumps({"total_sentences": 4, "matched_sentences": 2})
        db.add(parent)
        db.flush()
        child = LegalConsultation(
            user_id=self.user_id, question="[追问] 补充材料", category="labor",
            known_facts_json="[]", missing_facts_json="[]",
            references_json=json.dumps([
                {"title": "劳动合同法", "source_id": 1, "status": "active"},
                {"title": "实施条例", "source_id": 2, "status": "active"},
            ], ensure_ascii=False),
            advice="补充后答复", risk_level="low", status="draft", parent_id=parent.id,
        )
        child.attribution_json = json.dumps({"total_sentences": 4, "matched_sentences": 4})
        db.add(child)
        db.commit()
        child_id = child.id
        db.close()

        response = self.client.get(f"/api/legal/consultations/{child_id}", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertIn("comparison", data)
        self.assertEqual(data["comparison"]["references"]["before"], 1)
        self.assertEqual(data["comparison"]["references"]["after"], 2)
        self.assertEqual([ref["title"] for ref in data["comparison"]["references"]["added"]], ["实施条例"])
        self.assertEqual(data["comparison"]["level"], {"before": "partial", "after": "strong"})

    def test_review_queue_carries_chain_signal(self):
        """审核队列行带 evidence_summary，前端才能给无依据咨询打标签。"""
        db = self.Session()
        db.add(LegalConsultation(
            user_id=self.user_id, question="待审：无依据咨询", category="labor",
            known_facts_json="[]", missing_facts_json="[]", references_json="[]",
            advice="建议咨询律师。", risk_level="low", status="pending_review",
        ))
        db.commit()
        db.close()

        response = self.client.get("/api/legal/review-queue", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        rows = [row for row in response.json()["data"] if row["target_type"] == "consultation"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["evidence_summary"]["level"], "none")
        self.assertEqual(rows[0]["evidence_summary"]["references"], 0)

    def test_invalid_level_rejected(self):
        response = self.client.get("/api/legal/consultations?evidence_level=whatever", headers=self.headers)
        self.assertEqual(response.status_code, 422)

    def _add_pending(self, question, references):
        db = self.Session()
        db.add(LegalConsultation(
            user_id=self.user_id, question=question, category="labor",
            known_facts_json="[]", missing_facts_json="[]",
            references_json=json.dumps(references, ensure_ascii=False),
            advice="建议。", risk_level="low", status="pending_review",
        ))
        db.commit()
        db.close()

    def test_review_queue_filters_by_evidence_level(self):
        """队列筛选与服务端同一口径：只看该等级的咨询，合同/文书不返回。"""
        from app.models.legal import ContractReview

        self._add_pending("待审：无依据咨询", [])
        self._add_pending("待审：有依据咨询", [{"title": "劳动合同法", "status": "active"}])
        db = self.Session()
        db.add(ContractReview(
            user_id=self.user_id, title="待审合同", content="正文", summary="",
            risks_json="[]", references_json="[]", status="pending_review", version=1,
        ))
        db.commit()
        db.close()

        none_rows = self.client.get(
            "/api/legal/review-queue?evidence_level=none", headers=self.headers,
        ).json()["data"]
        self.assertEqual([row["question"] for row in none_rows], ["待审：无依据咨询"])
        self.assertEqual(none_rows[0]["target_type"], "consultation")

        cited_rows = self.client.get(
            "/api/legal/review-queue?evidence_level=partial", headers=self.headers,
        ).json()["data"]
        self.assertEqual([row["question"] for row in cited_rows], ["待审：有依据咨询"])

        all_rows = self.client.get("/api/legal/review-queue", headers=self.headers).json()["data"]
        self.assertEqual(len(all_rows), 3)  # 两条咨询 + 一条合同，无筛选时都在

    def test_review_queue_invalid_evidence_level_rejected(self):
        response = self.client.get("/api/legal/review-queue?evidence_level=whatever", headers=self.headers)
        self.assertEqual(response.status_code, 422)


class CompareConsultationEvidenceTests(unittest.TestCase):
    """追问前后：引用增删、句级覆盖、等级/忠实度变化。"""

    def _row(self, refs, total, matched, faithful=None):
        row = LegalConsultation(
            user_id=1, question="q", category="labor",
            known_facts_json="[]", missing_facts_json="[]",
            references_json=json.dumps(refs, ensure_ascii=False),
            advice="a", risk_level="low", status="draft",
        )
        row.id = 12
        row.attribution_json = json.dumps({"total_sentences": total, "matched_sentences": matched})
        if faithful is not None:
            row.faithfulness_json = json.dumps({"available": True, "faithful": faithful})
        return row

    def test_added_reference_and_coverage_improvement(self):
        prev = self._row([{"title": "劳动合同法", "source_id": 1, "status": "active"}], 4, 2)
        cur = self._row(
            [{"title": "劳动合同法", "source_id": 1, "status": "active"},
             {"title": "实施条例", "source_id": 2, "status": "active"}], 4, 4,
        )
        result = compare_consultation_evidence(prev, cur)
        self.assertEqual(result["parent_id"], 12)
        self.assertEqual(result["references"]["before"], 1)
        self.assertEqual(result["references"]["after"], 2)
        self.assertEqual([ref["title"] for ref in result["references"]["added"]], ["实施条例"])
        self.assertEqual(result["references"]["removed"], [])
        self.assertEqual(result["attribution"]["before"]["rate"], 50)
        self.assertEqual(result["attribution"]["after"]["rate"], 100)
        self.assertEqual(result["level"], {"before": "partial", "after": "strong"})
        self.assertIn("引用 1 → 2（+1/-0）", result["summary"])
        self.assertIn("50% → 100%", result["summary"])

    def test_removed_reference_is_reported(self):
        prev = self._row([{"title": "旧法", "source_id": 9, "status": "active"}], 2, 2)
        cur = self._row([], 0, 0)
        result = compare_consultation_evidence(prev, cur)
        self.assertEqual([ref["title"] for ref in result["references"]["removed"]], ["旧法"])
        self.assertEqual(result["level"], {"before": "strong", "after": "none"})
        self.assertIn("100% → —%", result["summary"])

    def test_faithfulness_change_is_reported(self):
        prev = self._row([{"title": "劳动合同法", "source_id": 1}], 2, 1)
        cur = self._row([{"title": "劳动合同法", "source_id": 1}], 2, 1, faithful=False)
        result = compare_consultation_evidence(prev, cur)
        self.assertEqual(result["faithfulness"], {"before": "unchecked", "after": "fail"})
