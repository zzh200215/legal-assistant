"""合同台账：每份合同补「最近一次审查」摘要（跨案件视图）。"""

import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import hash_password
from app.core.database import Base
from app.models.legal import ContractReview
from app.models.legal_contract import LegalContract, LegalContractVersion
from app.models.org import Organization
from app.models.user import User
from app.services.legal.legal_contract_listing import list_visible_contracts


class ContractListingReviewSummaryTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:", future=True,
            connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        self.db = self.Session()
        self.org = Organization(name="台账律所", code="ledger_firm")
        self.db.add(self.org)
        self.db.flush()
        self.user = User(
            username="ledger-user", email="ledger@example.com",
            hashed_password=hash_password("pw"), organization_id=self.org.id,
        )
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.org)
        self.db.refresh(self.user)

    def tearDown(self):
        self.db.close()

    def _contract(self, title):
        contract = LegalContract(
            organization_id=self.org.id, contract_no=f"NO-{title}", title=title, counterparty="对方",
            contract_type="service", status="active", created_by=self.user.id, version=1,
        )
        self.db.add(contract)
        self.db.commit()
        self.db.refresh(contract)
        return contract

    def test_attaches_latest_review_summary(self):
        contract = self._contract("有审查的合同")
        review = ContractReview(
            user_id=self.user.id, title=contract.title, content="正文", summary="",
            risks_json=json.dumps([
                {"clause_type": "breach", "risk_level": "high", "description": "违约金过高",
                 "source_location": {"paragraph": 1, "snippet": "正文"}},
                {"clause_type": "payment", "risk_level": "medium", "description": "付款不清",
                 "source_location": {}},
            ], ensure_ascii=False),
            references_json="[]", status="needs_lawyer_review", version=2,
        )
        self.db.add(review)
        self.db.commit()
        self.db.refresh(review)
        self.db.add(LegalContractVersion(
            contract_id=contract.id, organization_id=self.org.id, version_no=2,
            source_type="contract_review", text_snapshot="正文",
            source_review_id=review.id, created_by=self.user.id,
        ))
        self.db.commit()

        result = list_visible_contracts(self.db, org_id=self.org.id, user_id=self.user.id)
        item = next(row for row in result["items"] if row["id"] == contract.id)
        self.assertIsNotNone(item["review"])
        self.assertEqual(item["review"]["id"], review.id)
        self.assertEqual(item["review"]["status"], "needs_lawyer_review")
        self.assertEqual(item["review"]["risks_total"], 2)
        self.assertEqual(item["review"]["risks_high"], 1)
        self.assertEqual(item["review"]["risks_unanchored"], 1)

    def test_contract_without_review_has_null_summary(self):
        contract = self._contract("没有审查的合同")
        result = list_visible_contracts(self.db, org_id=self.org.id, user_id=self.user.id)
        item = next(row for row in result["items"] if row["id"] == contract.id)
        self.assertIsNone(item["review"])
