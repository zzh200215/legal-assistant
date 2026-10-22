"""跨审查风险统计（派生视图）：聚合口径 + 接口可见性。"""

import json
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import create_access_token, hash_password
from app.core.database import Base, get_db
from app.main import app
from app.models.legal import ContractReview
from app.models.user import User
from app.services.legal.contract_risk_stats import summarize_contract_risk_stats


def _row(risks, level="strong", unanchored=0):
    return {
        "id": 1,
        "risks": risks,
        "evidence_summary": {"level": level, "unanchored": unanchored},
    }


def _risk(severity="high", status=None, clause="breach", anchored=True):
    risk = {
        "clause_type": clause,
        "risk_level": severity,
        "description": "desc",
        "evidence": {"anchored": anchored},
    }
    if status is not None:
        risk["status"] = status
    return risk


class SummarizeRiskStatsTests(unittest.TestCase):
    def test_empty_input(self):
        stats = summarize_contract_risk_stats([])
        self.assertEqual(stats["reviews"], 0)
        self.assertEqual(stats["risks"], 0)
        self.assertEqual(stats["located_rate"], 0.0)
        self.assertEqual(stats["by_evidence_level"]["none"], 0)

    def test_counts_severity_status_and_gaps(self):
        rows = [
            _row([_risk("high"), _risk("medium", anchored=False)], level="partial", unanchored=1),
            _row([_risk("high", status="dismissed"), _risk("low", clause="payment")]),
        ]
        stats = summarize_contract_risk_stats(rows)
        self.assertEqual(stats["reviews"], 2)
        self.assertEqual(stats["risks"], 4)
        self.assertEqual(stats["by_severity"]["high"], 2)
        self.assertEqual(stats["by_severity"]["medium"], 1)
        self.assertEqual(stats["by_severity"]["critical"], 0)
        # 未写 status 的高风险归一为待审核；显式 dismissed 保留
        self.assertEqual(stats["by_status"]["needs_review"], 1)
        self.assertEqual(stats["by_status"]["dismissed"], 1)
        self.assertEqual(stats["by_status"]["open"], 2)
        self.assertEqual(stats["anchored"], 3)
        self.assertEqual(stats["unanchored"], 1)
        self.assertEqual(stats["located_rate"], 0.75)
        self.assertEqual(stats["high_total"], 2)
        self.assertEqual(stats["pending_high"], 1)
        self.assertEqual(stats["reviews_with_gaps"], 1)
        self.assertEqual(stats["by_evidence_level"]["partial"], 1)
        self.assertEqual(stats["by_evidence_level"]["strong"], 1)

    def test_clause_types_sorted_and_capped(self):
        risks = [_risk(clause="payment") for _ in range(3)] + [_risk(clause="breach") for _ in range(2)]
        risks += [_risk(clause=f"other{i}") for i in range(3)]
        stats = summarize_contract_risk_stats([_row(risks)])
        self.assertEqual(stats["by_clause_type"][0], {"clause_type": "payment", "count": 3})
        self.assertEqual(len(stats["by_clause_type"]), 5)

    def test_unknown_severity_falls_back_to_medium(self):
        stats = summarize_contract_risk_stats([_row([_risk(severity="weird")])])
        self.assertEqual(stats["by_severity"]["medium"], 1)


class RiskStatsApiTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        self.db = self.TestingSessionLocal()
        self.user = User(username="stats_owner", email="stats_owner@example.com",
                         hashed_password=hash_password("secret"), role="user")
        self.other = User(username="stats_other", email="stats_other@example.com",
                          hashed_password=hash_password("secret"), role="user")
        self.db.add_all([self.user, self.other])
        self.db.commit()

        def override_get_db():
            db = self.TestingSessionLocal()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)
        self.headers = {"Authorization": f"Bearer {create_access_token({'sub': self.user.id})}"}
        self.other_headers = {"Authorization": f"Bearer {create_access_token({'sub': self.other.id})}"}

        self.db.add(ContractReview(
            user_id=self.user.id, title="A", content="合同 A 正文", summary="",
            risks_json=json.dumps([
                {"clause_type": "breach", "risk_level": "high", "description": "违约金过高",
                 "source_location": {"paragraph": 1, "snippet": "合同 A 正文"}},
                {"clause_type": "payment", "risk_level": "low", "description": "付款节点不清",
                 "source_location": {"paragraph": None, "snippet": ""}},
            ], ensure_ascii=False),
            references_json="[]", status="pending_review", version=1,
        ))
        self.db.add(ContractReview(
            user_id=self.user.id, title="B", content="合同 B 正文", summary="",
            risks_json=json.dumps([
                {"clause_type": "breach", "risk_level": "high", "description": "责任不明",
                 "source_location": {"paragraph": None, "snippet": ""}},
            ], ensure_ascii=False),
            references_json="[]", status="pending_review", version=1,
        ))
        self.db.commit()

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def test_stats_for_own_reviews(self):
        response = self.client.get("/api/legal/contract-reviews/risk-stats", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["scope"], "mine")
        self.assertEqual(data["reviews"], 2)
        self.assertEqual(data["risks"], 3)
        self.assertEqual(data["by_severity"]["high"], 2)
        self.assertEqual(data["anchored"], 1)
        self.assertEqual(data["unanchored"], 2)
        self.assertEqual(data["pending_high"], 2)
        self.assertEqual(data["reviews_with_gaps"], 2)

    def test_other_user_sees_empty_stats(self):
        response = self.client.get("/api/legal/contract-reviews/risk-stats", headers=self.other_headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["reviews"], 0)

    def test_case_scope_requires_access(self):
        # 记录没有归案件，case_id 过滤后为空；无权限的 case 由 verify_case_access 拒绝
        response = self.client.get("/api/legal/contract-reviews/risk-stats?case_id=999", headers=self.headers)
        self.assertIn(response.status_code, (403, 404))
