"""迁移 0108 的存量回填：按最新带审查来源的版本生成摘要行。"""
import importlib.util
import json
import unittest
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import hash_password
from app.core.database import Base
from app.models.legal import ContractReview
from app.models.legal_contract import LegalContract, LegalContractVersion
from app.models.org import Organization
from app.models.user import User

MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / \
    "20261027_0108_contract_review_summary.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("mig_0108", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ContractSummaryBackfillTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)()

        org = Organization(name="回填律所", code="BACKFILL")
        self.db.add(org)
        self.db.flush()
        self.org_id = org.id
        user = User(username="backfill-user", email="backfill@example.com",
                    hashed_password=hash_password("pw"), organization_id=org.id)
        self.db.add(user)
        self.db.commit()
        self.user_id = user.id
        self.org_id = org.id

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _contract(self, title):
        contract = LegalContract(
            organization_id=self.org_id, contract_no=f"BF-{title}", title=title,
            contract_type="service", status="active", created_by=self.user_id, version=1,
        )
        self.db.add(contract)
        self.db.commit()
        self.db.refresh(contract)
        return contract

    def _review(self, contract, risks, status="needs_lawyer_review"):
        review = ContractReview(
            user_id=self.user_id, title=contract.title, content="正文", summary="",
            risks_json=json.dumps(risks, ensure_ascii=False), references_json="[]",
            status=status, version=1,
        )
        self.db.add(review)
        self.db.commit()
        self.db.refresh(review)
        return review

    def _version(self, contract, review, version_no):
        self.db.add(LegalContractVersion(
            contract_id=contract.id, organization_id=self.org_id, version_no=version_no,
            source_type="contract_review", text_snapshot="正文",
            source_review_id=review.id if review else None, created_by=self.user_id,
        ))
        self.db.commit()

    def _run_backfill(self):
        module = _load_migration()
        with self.engine.connect() as connection:
            module._backfill(connection)
            connection.commit()
            rows = connection.execute(select(module._summaries())).all()
        return rows

    def test_backfills_latest_review_counts(self):
        contract = self._contract("有审查的合同")
        review = self._review(contract, [
            {"risk_level": "high", "source_location": {"paragraph": 2}},
            {"risk_level": "medium", "source_location": {}},
            "not-a-dict",
        ])
        self._version(contract, review, 1)

        rows = self._run_backfill()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.contract_id, contract.id)
        self.assertEqual(row.review_id, review.id)
        self.assertEqual(row.review_status, "needs_lawyer_review")
        self.assertEqual(row.risks_total, 2)
        self.assertEqual(row.risks_high, 1)
        self.assertEqual(row.risks_unanchored, 1)

    def test_skips_contracts_without_review_versions(self):
        contract = self._contract("未审查合同")
        self._version(contract, None, 1)
        self.assertEqual(self._run_backfill(), [])

    def test_uses_highest_version_with_review(self):
        contract = self._contract("多版本合同")
        old_review = self._review(contract, [{"risk_level": "high", "source_location": {}}])
        new_review = self._review(contract, [{"risk_level": "medium", "source_location": {"paragraph": 1}}])
        self._version(contract, old_review, 1)
        self._version(contract, new_review, 2)

        rows = self._run_backfill()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].review_id, new_review.id)
        self.assertEqual(rows[0].risks_high, 0)
        self.assertEqual(rows[0].risks_unanchored, 0)


if __name__ == "__main__":
    unittest.main()
