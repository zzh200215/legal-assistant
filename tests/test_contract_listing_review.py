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
            sort="reviewed_at", order="desc", page_size=20,
        )
        self.assertEqual([row["id"] for row in page["items"]][:3], [newer.id, same_time.id, older.id])

    def test_search_matches_title_counterparty_and_contract_no(self):
        labor = self._contract("劳动合同模板")
        labor.counterparty = "某科技公司"
        labor.contract_no = "HT-2026-001"
        purchase = self._contract("采购协议")
        purchase.counterparty = "某物流公司"
        purchase.contract_no = "HT-2026-002"
        self.db.commit()

        def ids(**kwargs):
            return [row["id"] for row in list_visible_contracts(
                self.db, org_id=self.org.id, user_id=self.user.id, **kwargs,
            )["items"]]

        self.assertEqual(ids(q="劳动"), [labor.id])
        self.assertEqual(ids(q=" 劳动 "), [labor.id])  # 前后空白被归一
        self.assertEqual(ids(q="科技"), [labor.id])  # 相对方
        self.assertEqual(ids(q="HT-2026-002"), [purchase.id])  # 合同编号
        self.assertEqual(ids(q="劳"), [labor.id])  # 单字：SQLite/非 MySQL 回退 LIKE
        self.assertEqual(ids(q="%"), [])  # LIKE 通配符按字面量处理
        self.assertEqual(ids(q="不存在的关键词"), [])

        # 命中字段提示：给前端高亮用，展示口径按字面子串
        by_title = list_visible_contracts(
            self.db, org_id=self.org.id, user_id=self.user.id, q="劳动",
        )["items"][0]
        self.assertEqual(by_title["search"]["query"], "劳动")
        self.assertEqual(by_title["search"]["matched_fields"], ["title"])
        self.assertEqual(by_title["search"]["snippets"]["title"], "劳动合同模板")
        by_party = list_visible_contracts(
            self.db, org_id=self.org.id, user_id=self.user.id, q="科技",
        )["items"][0]
        self.assertEqual(by_party["search"]["matched_fields"], ["counterparty"])
        by_no = list_visible_contracts(
            self.db, org_id=self.org.id, user_id=self.user.id, q="HT-2026-002",
        )["items"][0]
        self.assertEqual(by_no["search"]["matched_fields"], ["contract_no"])
        no_query = list_visible_contracts(
            self.db, org_id=self.org.id, user_id=self.user.id,
        )["items"][0]
        self.assertIsNone(no_query["search"])

    def test_search_pagination_reuses_query_and_rejects_other_query(self):
        for index in range(3):
            self._contract(f"劳动事务{index}")
        first = list_visible_contracts(
            self.db, org_id=self.org.id, user_id=self.user.id,
            q="劳动", page_size=2,
        )
        self.assertEqual(first["total"], 3)
        self.assertEqual(len(first["items"]), 2)
        self.assertTrue(first["has_more"])

        second = list_visible_contracts(
            self.db, org_id=self.org.id, user_id=self.user.id,
            q="劳动", page_size=2, cursor=first["next_cursor"],
        )
        self.assertEqual(len(second["items"]), 1)
        self.assertFalse(second["has_more"])

        with self.assertRaisesRegex(ValueError, "筛选"):
            list_visible_contracts(
                self.db, org_id=self.org.id, user_id=self.user.id,
                q="采购", page_size=2, cursor=first["next_cursor"],
            )

    def test_search_snippets_keep_20_chars_around_match(self):
        long = self._contract("甲" * 30 + "劳动合同" + "乙" * 30)
        long.counterparty = "丙" * 25 + "科技" + "丁" * 25
        long.contract_no = "HT-" + "0" * 20 + "劳动" + "9" * 20
        short = self._contract("劳动合同")
        self.db.commit()

        def row_for(query, contract_id):
            result = list_visible_contracts(
                self.db, org_id=self.org.id, user_id=self.user.id, q=query,
            )
            return next(row for row in result["items"] if row["id"] == contract_id)

        by_title = row_for("劳动", long.id)
        self.assertEqual(
            by_title["search"]["snippets"]["title"],
            "…" + "甲" * 20 + "劳动合同" + "乙" * 18 + "…",
        )
        self.assertEqual(
            by_title["search"]["snippets"]["contract_no"],
            "…" + "0" * 20 + "劳动" + "9" * 20,
        )
        by_party = row_for("科技", long.id)
        self.assertEqual(
            by_party["search"]["snippets"]["counterparty"],
            "…" + "丙" * 20 + "科技" + "丁" * 20 + "…",
        )
        # 短字段不补省略号
        self.assertEqual(
            row_for("劳动", short.id)["search"]["snippets"]["title"], "劳动合同",
        )

    def test_relevance_sort_orders_by_matched_field_and_paginates(self):
        title_hit = self._contract("劳动合同")
        title_hit.counterparty = "甲公司"
        title_hit.contract_no = "HT-001"
        party_hit = self._contract("服务合同")
        party_hit.counterparty = "劳动公司"
        party_hit.contract_no = "HT-002"
        no_hit = self._contract("采购合同")
        no_hit.counterparty = "乙公司"
        no_hit.contract_no = "LD-劳动-003"
        self.db.commit()

        first = list_visible_contracts(
            self.db, org_id=self.org.id, user_id=self.user.id,
            q="劳动", sort="relevance", order="desc", page_size=2,
        )
        self.assertEqual(
            [row["id"] for row in first["items"]], [title_hit.id, party_hit.id],
        )
        self.assertEqual(first["sort"], "relevance")
        self.assertEqual(first["order"], "desc")
        self.assertTrue(first["has_more"])

        second = list_visible_contracts(
            self.db, org_id=self.org.id, user_id=self.user.id,
            q="劳动", sort="relevance", order="desc", page_size=2,
            cursor=first["next_cursor"],
        )
        self.assertEqual([row["id"] for row in second["items"]], [no_hit.id])
        self.assertFalse(second["has_more"])

    def test_relevance_sort_requires_query(self):
        with self.assertRaisesRegex(ValueError, "相关度"):
            list_visible_contracts(
                self.db, org_id=self.org.id, user_id=self.user.id, sort="relevance",
            )

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
        first = list_visible_contracts(
            self.db, org_id=self.org.id, user_id=self.user.id,
            review_status="needs_lawyer_review", page_size=2,
        )
        self.assertEqual(first["total"], 3)
        self.assertEqual(len(first["items"]), 2)
        self.assertTrue(first["has_more"])
        self.assertTrue(first["next_cursor"])

        second = list_visible_contracts(
            self.db, org_id=self.org.id, user_id=self.user.id,
            review_status="needs_lawyer_review", page_size=2, cursor=first["next_cursor"],
        )
        self.assertEqual(second["total"], 3)  # 总数沿用游标快照，不重新 count
        self.assertEqual(len(second["items"]), 1)
        self.assertFalse(second["has_more"])
        self.assertIsNone(second["next_cursor"])
        # 两页没有重复
        first_ids = {row["id"] for row in first["items"]}
        self.assertTrue(first_ids.isdisjoint({row["id"] for row in second["items"]}))

    def test_keyset_pagination_does_not_drift_when_rows_change(self):
        base = datetime(2026, 9, 1, 9, 0, 0)
        contracts = []
        for index in range(5):
            contract = self._contract(f"翻页合同{index}")
            contract.updated_at = base + timedelta(minutes=index)
            contracts.append(contract)
        self.db.commit()

        first = list_visible_contracts(
            self.db, org_id=self.org.id, user_id=self.user.id,
            sort="updated_at", order="desc", page_size=2,
        )
        self.assertEqual(
            [row["id"] for row in first["items"]],
            [contracts[4].id, contracts[3].id],
        )
        cursor = first["next_cursor"]
        self.assertTrue(cursor)

        # 翻页期间把第一页最后一条改到最前：游标仍按旧键往后走，
        # 第二页不重复第一页，也不因为整体位移而跳过 contracts[2] / contracts[1]。
        contracts[3].updated_at = base + timedelta(days=1)
        self.db.commit()

        second = list_visible_contracts(
            self.db, org_id=self.org.id, user_id=self.user.id,
            sort="updated_at", order="desc", page_size=2, cursor=cursor,
        )
        self.assertEqual(
            [row["id"] for row in second["items"]],
            [contracts[2].id, contracts[1].id],
        )

        third = list_visible_contracts(
            self.db, org_id=self.org.id, user_id=self.user.id,
            sort="updated_at", order="desc", page_size=2, cursor=second["next_cursor"],
        )
        self.assertEqual([row["id"] for row in third["items"]], [contracts[0].id])
        self.assertFalse(third["has_more"])

    def test_keyset_review_sort_splits_value_and_unreviewed_tail(self):
        base = datetime(2026, 9, 1, 9, 0, 0)
        reviewed = []
        for index in range(3):
            contract = self._contract(f"已审查{index}")
            self._attach_review(
                contract, status="lawyer_approved", risks=[],
                reviewed_at=base + timedelta(hours=index),
            )
            reviewed.append(contract)
        unreviewed = [self._contract(f"未审查{index}") for index in range(3)]

        first = list_visible_contracts(
            self.db, org_id=self.org.id, user_id=self.user.id,
            sort="reviewed_at", order="desc", page_size=4,
        )
        self.assertEqual(
            [row["id"] for row in first["items"]],
            [reviewed[2].id, reviewed[1].id, reviewed[0].id, unreviewed[2].id],
        )
        self.assertTrue(first["has_more"])

        second = list_visible_contracts(
            self.db, org_id=self.org.id, user_id=self.user.id,
            sort="reviewed_at", order="desc", page_size=4, cursor=first["next_cursor"],
        )
        self.assertEqual(
            [row["id"] for row in second["items"]],
            [unreviewed[1].id, unreviewed[0].id],
        )
        self.assertFalse(second["has_more"])
        self.assertIsNone(second["next_cursor"])

    def test_cursor_rejects_changed_filters_sort_and_tampering(self):
        for index in range(2):
            self._contract(f"游标合同{index}")
        first = list_visible_contracts(
            self.db, org_id=self.org.id, user_id=self.user.id, page_size=1,
        )
        cursor = first["next_cursor"]
        self.assertTrue(cursor)

        with self.assertRaisesRegex(ValueError, "筛选"):
            list_visible_contracts(
                self.db, org_id=self.org.id, user_id=self.user.id,
                risk="high", page_size=1, cursor=cursor,
            )
        with self.assertRaisesRegex(ValueError, "排序"):
            list_visible_contracts(
                self.db, org_id=self.org.id, user_id=self.user.id,
                sort="reviewed_at", page_size=1, cursor=cursor,
            )
        with self.assertRaisesRegex(ValueError, "游标"):
            list_visible_contracts(
                self.db, org_id=self.org.id, user_id=self.user.id,
                page_size=1, cursor="not-a-cursor",
            )

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
            review_status="needs_lawyer_review", page_size=20,
        )
        self.assertEqual(result["total"], 1)
        self.assertEqual([item["id"] for item in result["items"]], [oldest.id])
        # 未审查筛选同样走 SQL：1000 条未审查都能被统计（旧实现只取前 1000 条内存过滤）
        none_result = list_visible_contracts(
            self.db, org_id=self.org.id, user_id=self.user.id,
            risk="none", page_size=20,
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
