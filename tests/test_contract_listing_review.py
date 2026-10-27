"""合同台账：审查摘要物化（筛选下沉 SQL）、严重度细分、按审查时间排序、写入路径刷新。"""

import json
import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import hash_password
from app.core.database import Base
from app.core.time import utc_now
from app.models.legal import ContractReview, LegalCase
from app.models.legal_contract import LegalContract, LegalContractReviewSummary, LegalContractVersion
from app.models.legal_portal import LegalCaseMember
from app.models.org import Organization
from app.models.user import User
from app.services.legal.contract_review_summary import (
    refresh_contract_review_summary, refresh_summaries_for_review,
)
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

    def _contract(self, title, *, case_id=None):
        contract = LegalContract(
            organization_id=self.org.id, contract_no=f"NO-{title}", title=title, counterparty="对方",
            contract_type="service", status="active", case_id=case_id,
            created_by=self.user.id, version=1,
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
        refresh_contract_review_summary(self.db, contract.id)
        self.db.commit()

        result = list_visible_contracts(self.db, org_id=self.org.id, user_id=self.user.id)
        item = next(row for row in result["items"] if row["id"] == contract.id)
        self.assertIsNotNone(item["review"])
        self.assertEqual(item["review"]["id"], review.id)
        self.assertEqual(item["review"]["status"], "needs_lawyer_review")
        self.assertEqual(item["review"]["risks_total"], 2)
        self.assertEqual(item["review"]["risks_high"], 1)
        self.assertEqual(item["review"]["risks_critical"], 0)
        self.assertEqual(item["review"]["risks_medium"], 1)
        self.assertEqual(item["review"]["risks_low"], 0)
        self.assertEqual(item["review"]["risks_unanchored"], 1)

    def test_contract_without_review_has_null_summary(self):
        contract = self._contract("没有审查的合同")
        result = list_visible_contracts(self.db, org_id=self.org.id, user_id=self.user.id)
        item = next(row for row in result["items"] if row["id"] == contract.id)
        self.assertIsNone(item["review"])

    def _attach_review(self, contract, *, status, risks, reviewed_at=None):
        review = ContractReview(
            user_id=self.user.id, title=contract.title, content="正文", summary="",
            risks_json=json.dumps(risks, ensure_ascii=False),
            references_json="[]", status=status, version=1, reviewed_at=reviewed_at,
        )
        self.db.add(review)
        self.db.commit()
        self.db.refresh(review)
        self.db.add(LegalContractVersion(
            contract_id=contract.id, organization_id=self.org.id, version_no=1,
            source_type="contract_review", text_snapshot="正文",
            source_review_id=review.id, created_by=self.user.id,
        ))
        self.db.commit()
        refresh_contract_review_summary(self.db, contract.id)
        self.db.commit()
        return review

    def test_filters_by_review_status_and_risk(self):
        high = self._contract("高风险合同")
        self._attach_review(high, status="needs_lawyer_review", risks=[
            {"clause_type": "breach", "risk_level": "high", "description": "高",
             "source_location": {"paragraph": 1}},
        ])
        low = self._contract("已通过合同")
        self._attach_review(low, status="lawyer_approved", risks=[
            {"clause_type": "payment", "risk_level": "low", "description": "低",
             "source_location": {"paragraph": 1}},
        ])
        none = self._contract("未审查合同")

        by_status = list_visible_contracts(
            self.db, org_id=self.org.id, user_id=self.user.id, review_status="needs_lawyer_review",
        )
        self.assertEqual([item["id"] for item in by_status["items"]], [high.id])
        self.assertEqual(by_status["total"], 1)

        by_high = list_visible_contracts(self.db, org_id=self.org.id, user_id=self.user.id, risk="high")
        self.assertEqual([item["id"] for item in by_high["items"]], [high.id])

        by_none = list_visible_contracts(self.db, org_id=self.org.id, user_id=self.user.id, risk="none")
        self.assertEqual([item["id"] for item in by_none["items"]], [none.id])

        all_rows = list_visible_contracts(self.db, org_id=self.org.id, user_id=self.user.id)
        self.assertEqual(all_rows["total"], 3)

    def test_severity_breakdown_and_filters(self):
        """严重度细分：critical 是 high 的子集，未知级别按 medium 计，四档相加等于总数。"""
        contract = self._contract("严重度合同")
        self._attach_review(contract, status="needs_lawyer_review", risks=[
            {"risk_level": "critical", "description": "严重", "source_location": {"paragraph": 1}},
            {"risk_level": "high", "description": "高", "source_location": {"paragraph": 2}},
            {"risk_level": "medium", "description": "中", "source_location": {"paragraph": 3}},
            {"risk_level": "low", "description": "低", "source_location": {"paragraph": 4}},
            {"description": "未标级别", "source_location": {"paragraph": 5}},
        ])
        low_only = self._contract("只有低风险")
        self._attach_review(low_only, status="lawyer_approved", risks=[
            {"risk_level": "low", "description": "低", "source_location": {"paragraph": 1}},
        ])

        all_rows = list_visible_contracts(self.db, org_id=self.org.id, user_id=self.user.id)
        item = next(row for row in all_rows["items"] if row["id"] == contract.id)
        review = item["review"]
        self.assertEqual(review["risks_total"], 5)
        self.assertEqual(review["risks_high"], 2)  # high + critical
        self.assertEqual(review["risks_critical"], 1)
        self.assertEqual(review["risks_medium"], 2)  # medium + 未知级别
        self.assertEqual(review["risks_low"], 1)
        self.assertEqual(
            review["risks_high"] + review["risks_medium"] + review["risks_low"],
            review["risks_total"],
        )

        expectations = {
            "critical": [contract.id],
            "high": [contract.id],
            "medium": [contract.id],
            "low": [low_only.id, contract.id],
        }
        for risk, expected_ids in expectations.items():
            result = list_visible_contracts(
                self.db, org_id=self.org.id, user_id=self.user.id, risk=risk,
            )
            self.assertEqual(sorted(row["id"] for row in result["items"]), sorted(expected_ids),
                             msg=f"risk={risk}")
            self.assertEqual(result["total"], len(expected_ids), msg=f"risk={risk}")

    def test_sort_by_reviewed_at_puts_unreviewed_last(self):
        base = datetime(2026, 9, 1, 9, 0, 0)
        older = self._contract("较早审查")
        self._attach_review(older, status="lawyer_approved", risks=[], reviewed_at=base)
        newer = self._contract("较新审查")
        self._attach_review(
            newer, status="lawyer_approved", risks=[], reviewed_at=base + timedelta(days=2),
        )
        unreviewed = self._contract("未审查合同")

        desc = list_visible_contracts(
            self.db, org_id=self.org.id, user_id=self.user.id,
            sort="reviewed_at", order="desc",
        )
        self.assertEqual([row["id"] for row in desc["items"]], [newer.id, older.id, unreviewed.id])
        self.assertEqual(desc["sort"], "reviewed_at")
        self.assertEqual(desc["order"], "desc")

        asc = list_visible_contracts(
            self.db, org_id=self.org.id, user_id=self.user.id,
            sort="reviewed_at", order="asc",
        )
        self.assertEqual([row["id"] for row in asc["items"]], [older.id, newer.id, unreviewed.id])
        self.assertEqual(asc["order"], "asc")

        # 分页稳定：同一审查时间按合同 id 倒序
        same_time = self._contract("同一时间审查")
        self._attach_review(same_time, status="lawyer_approved", risks=[], reviewed_at=base)
        page = list_visible_contracts(
            self.db, org_id=self.org.id, user_id=self.user.id,
            sort="reviewed_at", order="desc", page=1, page_size=20,
        )
        self.assertEqual([row["id"] for row in page["items"]][:3], [newer.id, same_time.id, older.id])

    def test_sort_whitelist_falls_back_to_updated_at(self):
        contract = self._contract("回退排序合同")
        result = list_visible_contracts(
            self.db, org_id=self.org.id, user_id=self.user.id,
            sort="risks_high; drop table", order="sideways",
        )
        self.assertEqual(result["sort"], "updated_at")
        self.assertEqual(result["order"], "desc")
        self.assertEqual([row["id"] for row in result["items"]], [contract.id])

    def test_strict_case_contracts_visible_only_to_active_members(self):
        case = LegalCase(
            organization_id=self.org.id, user_id=self.user.id, title="严格案件",
            case_type="contract_dispute", status="in_progress", is_strict_mode=1,
        )
        self.db.add(case)
        self.db.commit()
        self.db.refresh(case)
        contract = self._contract("严格案件合同", case_id=case.id)

        hidden = list_visible_contracts(self.db, org_id=self.org.id, user_id=self.user.id)
        self.assertEqual(hidden["total"], 0)  # 非成员：严格案件合同不可见

        self.db.add_all([
            LegalCaseMember(case_id=case.id, organization_id=self.org.id, user_id=self.user.id,
                            case_role="owner", granted_by=self.user.id),
            LegalCaseMember(case_id=case.id, organization_id=self.org.id, user_id=self.user.id,
                            case_role="collaborator", granted_by=self.user.id),
        ])
        self.db.commit()
        visible = list_visible_contracts(self.db, org_id=self.org.id, user_id=self.user.id)
        self.assertEqual([row["id"] for row in visible["items"]], [contract.id])
        self.assertEqual(visible["total"], 1)  # 重复成员记录不会放大行数

        self.db.query(LegalCaseMember).filter(
            LegalCaseMember.case_id == case.id).update({"revoked_at": utc_now()})
        self.db.commit()
        revoked = list_visible_contracts(self.db, org_id=self.org.id, user_id=self.user.id)
        self.assertEqual(revoked["total"], 0)  # 撤销后不再可见

    def test_filtered_list_paginates_after_filtering(self):
        for index in range(3):
            contract = self._contract(f"待审合同{index}")
            self._attach_review(contract, status="needs_lawyer_review", risks=[])
        page = list_visible_contracts(
            self.db, org_id=self.org.id, user_id=self.user.id,
            review_status="needs_lawyer_review", page=2, page_size=2,
        )
        self.assertEqual(page["total"], 3)
        self.assertEqual(len(page["items"]), 1)

    def test_filter_pushes_down_beyond_previous_1000_cap(self):
        """审查筛选不再被 1000 条上限截断：匹配的合同排在第 1000 条之后也能被筛出来。"""
        oldest = self._contract("最早创建、带审查")
        self._attach_review(oldest, status="needs_lawyer_review", risks=[])
        others = [
            LegalContract(
                organization_id=self.org.id, contract_no=f"BULK-{index}", title=f"批量合同{index}",
                contract_type="service", status="active", created_by=self.user.id, version=1,
            )
            for index in range(1000)
        ]
        self.db.add_all(others)
        self.db.commit()

        result = list_visible_contracts(
            self.db, org_id=self.org.id, user_id=self.user.id,
            review_status="needs_lawyer_review", page=1, page_size=20,
        )
        self.assertEqual(result["total"], 1)
        self.assertEqual([item["id"] for item in result["items"]], [oldest.id])
        # 未审查筛选同样走 SQL：1000 条未审查都能被统计（旧实现只取前 1000 条内存过滤）
        none_result = list_visible_contracts(
            self.db, org_id=self.org.id, user_id=self.user.id,
            risk="none", page=1, page_size=20,
        )
        self.assertEqual(none_result["total"], 1000)
        self.assertEqual(len(none_result["items"]), 20)

    def test_summary_refreshes_on_review_action(self):
        contract = self._contract("审核流转合同")
        review = self._attach_review(contract, status="needs_lawyer_review", risks=[])
        reviewer = User(
            username="ledger-reviewer", email="reviewer@example.com",
            hashed_password=hash_password("pw"), role="admin",
            organization_id=self.org.id,
        )
        self.db.add(reviewer)
        self.db.commit()

        from app.services.legal.legal_workspace_service import legal_workspace_read_module

        legal_workspace_read_module.apply_review_action(
            self.db, reviewer, target_type="contract_review", target_id=review.id,
            action="approve", note="通过",
        )
        approved = list_visible_contracts(
            self.db, org_id=self.org.id, user_id=self.user.id, review_status="lawyer_approved",
        )
        self.assertEqual([item["id"] for item in approved["items"]], [contract.id])

    def test_summary_refreshes_when_risks_rewritten(self):
        from app.services.legal.legal_domain_service import persist_review_artifacts

        contract = self._contract("风险重写合同")
        review = self._attach_review(contract, status="needs_lawyer_review", risks=[])
        summary = self.db.query(LegalContractReviewSummary).filter(
            LegalContractReviewSummary.contract_id == contract.id).first()
        self.assertEqual(summary.risks_total, 0)

        persist_review_artifacts(self.db, review, risks=[
            {"risk_level": "high", "description": "高", "source_location": {"paragraph": 1}},
            {"risk_level": "medium", "description": "中", "source_location": {}},
        ], refs=[])
        self.db.refresh(summary)
        self.assertEqual(summary.risks_total, 2)
        self.assertEqual(summary.risks_high, 1)
        self.assertEqual(summary.risks_unanchored, 1)

        high = list_visible_contracts(
            self.db, org_id=self.org.id, user_id=self.user.id, risk="high",
        )
        self.assertEqual([item["id"] for item in high["items"]], [contract.id])

    def test_summary_removed_when_review_unlinked(self):
        contract = self._contract("解除关联合同")
        self._attach_review(contract, status="needs_lawyer_review", risks=[])
        self.db.query(LegalContractVersion).filter(
            LegalContractVersion.contract_id == contract.id).delete()
        self.db.commit()
        refresh_contract_review_summary(self.db, contract.id)
        self.db.commit()
        self.assertIsNone(self.db.query(LegalContractReviewSummary).filter(
            LegalContractReviewSummary.contract_id == contract.id).first())
        none_rows = list_visible_contracts(
            self.db, org_id=self.org.id, user_id=self.user.id, risk="none",
        )
        self.assertEqual([item["id"] for item in none_rows["items"]], [contract.id])

    def test_refresh_helper_returns_affected_contracts(self):
        contract = self._contract("刷新计数合同")
        review = self._attach_review(contract, status="needs_lawyer_review", risks=[])
        self.assertEqual(refresh_summaries_for_review(self.db, review.id), 1)
        self.assertEqual(refresh_summaries_for_review(self.db, review.id + 999), 0)
