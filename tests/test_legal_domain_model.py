"""P1 领域模型测试：实体/列/关联 + 法源适用性判定。

覆盖需求验收点：核心关联可查询(1)、法源适用性判定(5)。
"""
import unittest
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.legal import LegalSource, LegalReviewAction
from app.models.legal_domain import (
    ContractRiskItem,
    LegalClaim,
    LegalEvidence,
    LegalFact,
    LegalReference,
)
from app.services.legal.legal_reference_service import check_applicability


class LegalDomainModelTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        self.db = self.SessionLocal()

    def tearDown(self):
        self.db.close()

    def test_models_expose_expected_columns(self):
        # 验收1：核心实体与 Claim->Evidence->Reference 关联字段可查询
        self.assertIn("fact_type", LegalFact.__table__.columns.keys())
        self.assertIn("claim_id", LegalEvidence.__table__.columns.keys())
        self.assertIn("fact_id", LegalEvidence.__table__.columns.keys())
        self.assertIn("claim_type", LegalClaim.__table__.columns.keys())
        self.assertIn("risk_item_id", LegalClaim.__table__.columns.keys())
        self.assertIn("source_id", LegalReference.__table__.columns.keys())
        self.assertIn("claim_id", LegalReference.__table__.columns.keys())
        self.assertIn("review_id", ContractRiskItem.__table__.columns.keys())
        self.assertIn("contract_version_id", ContractRiskItem.__table__.columns.keys())
        self.assertIn("severity", ContractRiskItem.__table__.columns.keys())
        # 扩展列
        self.assertIn("expiration_date", LegalSource.__table__.columns.keys())
        self.assertIn("applicability_scope", LegalSource.__table__.columns.keys())
        self.assertIn("canonical_identifier", LegalSource.__table__.columns.keys())
        self.assertIn("target_version", LegalReviewAction.__table__.columns.keys())

    def test_claim_confidence_defaults_null(self):
        # 置信度无法可靠提供时不伪造精确分数
        claim = LegalClaim(user_id=1, source_type="contract_review", source_id=1,
                           claim_type="risk_warning", statement="x")
        self.db.add(claim)
        self.db.commit()
        self.assertIsNone(claim.confidence)

    def test_source_applicability_active(self):
        src = LegalSource(user_id=1, title="A", source_type="statute", content="x",
                          status="active", effective_date=date(2020, 1, 1),
                          expiration_date=date(2030, 1, 1), jurisdiction="中国大陆")
        verdict = check_applicability(src, analysis_date=date(2026, 1, 1), jurisdiction="中国大陆")
        self.assertIs(verdict["applicable"], True)

    def test_source_applicability_expired(self):
        src = LegalSource(user_id=1, title="B", source_type="statute", content="x",
                          status="active", effective_date=date(2015, 1, 1),
                          expiration_date=date(2021, 1, 1))
        verdict = check_applicability(src, analysis_date=date(2026, 1, 1))
        self.assertIs(verdict["applicable"], False)

    def test_source_applicability_not_yet_effective(self):
        src = LegalSource(user_id=1, title="C", source_type="statute", content="x",
                          status="active", effective_date=date(2027, 1, 1))
        verdict = check_applicability(src, analysis_date=date(2026, 1, 1))
        self.assertIs(verdict["applicable"], False)

    def test_source_applicability_inactive(self):
        src = LegalSource(user_id=1, title="D", source_type="statute", content="x", status="inactive")
        verdict = check_applicability(src, analysis_date=date(2026, 1, 1))
        self.assertIs(verdict["applicable"], False)

    def test_source_applicability_pending_update_unknown(self):
        src = LegalSource(user_id=1, title="E", source_type="statute", content="x", status="pending_update")
        verdict = check_applicability(src, analysis_date=date(2026, 1, 1))
        self.assertIsNone(verdict["applicable"])
        self.assertIn("待", verdict.get("reason") or "")

    def test_source_applicability_jurisdiction_mismatch_unknown(self):
        src = LegalSource(user_id=1, title="F", source_type="statute", content="x",
                          status="active", jurisdiction="香港")
        verdict = check_applicability(src, analysis_date=date(2026, 1, 1), jurisdiction="中国大陆")
        self.assertIsNone(verdict["applicable"])

    def test_source_applicability_unknown_status(self):
        src = LegalSource(user_id=1, title="G", source_type="statute", content="x", status="weird")
        verdict = check_applicability(src, analysis_date=date(2026, 1, 1))
        self.assertIsNone(verdict["applicable"])

    def test_reference_row_persists_application_verdict(self):
        src = LegalSource(user_id=1, title="H", source_type="statute", content="x",
                          status="active", effective_date=date(2020, 1, 1),
                          expiration_date=date(2030, 1, 1), jurisdiction="中国大陆")
        self.db.add(src)
        self.db.commit()
        self.db.refresh(src)
        ref = LegalReference(user_id=1, source_id=src.id, citation_text="民法典",
                             jurisdiction="中国大陆", applicable=1, applicability_note="现行有效且适用")
        self.db.add(ref)
        self.db.commit()
        self.db.refresh(ref)
        self.assertEqual(ref.applicable, 1)


if __name__ == "__main__":
    unittest.main()
