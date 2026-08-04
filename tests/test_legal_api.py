import io
import json
import unittest

from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import create_access_token, hash_password
from app.core.database import Base, get_db
from app.main import app
from app.models.legal import ContractReview, LegalArticle, LegalConsultation, LegalDraft, LegalSource
from app.models.user import User


class LegalApiTests(unittest.TestCase):
    """Phase 3: 法源批量导入、版本状态管理、检索测试工具"""

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

        self.admin = User(
            username="legal_admin",
            email="legal_admin@example.com",
            hashed_password=hash_password("secret"),
            role="admin",
        )
        self.member = User(
            username="legal_member",
            email="legal_member@example.com",
            hashed_password=hash_password("secret"),
            role="user",
        )
        self.db.add_all([self.admin, self.member])
        self.db.commit()
        self.db.refresh(self.admin)
        self.db.refresh(self.member)

        def override_get_db():
            db = self.TestingSessionLocal()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)
        self.admin_headers = {"Authorization": f"Bearer {create_access_token({'sub': self.admin.id})}"}
        self.member_headers = {"Authorization": f"Bearer {create_access_token({'sub': self.member.id})}"}

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    # ── 批量导入 ─────────────────────────────────────────────

    def test_import_csv_creates_sources(self):
        csv_content = (
            "title,source_type,content,citation,version,status\n"
            "《测试法规一》,statute,测试内容一,测试引用一,v1,active\n"
            "《测试法规二》,case,测试内容二,测试引用二,v1,pending_update\n"
        )
        files = {"file": ("sources.csv", io.BytesIO(csv_content.encode("utf-8-sig")), "text/csv")}
        response = self.client.post("/api/legal/sources/import", files=files, headers=self.admin_headers)
        self.assertEqual(response.status_code, 200)
        body = response.json()["data"]
        self.assertEqual(body["imported"], 2)
        self.assertEqual(body["skipped"], 0)

    def test_import_skips_rows_missing_required_fields(self):
        csv_content = "title,source_type,content\n,statute,\n"
        files = {"file": ("sources.csv", io.BytesIO(csv_content.encode("utf-8-sig")), "text/csv")}
        response = self.client.post("/api/legal/sources/import", files=files, headers=self.admin_headers)
        self.assertEqual(response.status_code, 200)
        body = response.json()["data"]
        self.assertEqual(body["imported"], 0)
        self.assertEqual(body["skipped"], 1)
        self.assertTrue(body["errors"])

    def test_import_rejects_missing_required_columns(self):
        csv_content = "title,content\n测试,内容\n"
        files = {"file": ("sources.csv", io.BytesIO(csv_content.encode("utf-8-sig")), "text/csv")}
        response = self.client.post("/api/legal/sources/import", files=files, headers=self.admin_headers)
        self.assertEqual(response.status_code, 400)

    def test_import_rejects_unsupported_file_type(self):
        files = {"file": ("sources.txt", io.BytesIO(b"not a csv"), "text/plain")}
        response = self.client.post("/api/legal/sources/import", files=files, headers=self.admin_headers)
        self.assertEqual(response.status_code, 400)

    def test_import_forbidden_for_non_admin(self):
        csv_content = "title,source_type,content\n测试,statute,内容\n"
        files = {"file": ("sources.csv", io.BytesIO(csv_content.encode("utf-8-sig")), "text/csv")}
        response = self.client.post("/api/legal/sources/import", files=files, headers=self.member_headers)
        self.assertEqual(response.status_code, 403)

    def test_import_xlsx_creates_sources(self):
        wb = Workbook()
        ws = wb.active
        ws.append(["title", "source_type", "content", "citation", "version", "status"])
        ws.append(["《测试法规Excel》", "statute", "测试内容", "测试引用", "v1", "active"])
        buffer = io.BytesIO()
        wb.save(buffer)
        buffer.seek(0)
        files = {"file": ("sources.xlsx", buffer, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        response = self.client.post("/api/legal/sources/import", files=files, headers=self.admin_headers)
        self.assertEqual(response.status_code, 200)
        body = response.json()["data"]
        self.assertEqual(body["imported"], 1)

    # ── 状态更新 ─────────────────────────────────────────────

    def test_update_source_status(self):
        source = LegalSource(
            user_id=self.admin.id, title="测试法规", source_type="statute",
            content="内容", status="active", version="v1",
        )
        self.db.add(source)
        self.db.commit()
        self.db.refresh(source)

        response = self.client.patch(
            f"/api/legal/sources/{source.id}/status",
            json={"status": "inactive"},
            headers=self.admin_headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["status"], "inactive")

    def test_update_source_status_rejects_invalid_value(self):
        source = LegalSource(
            user_id=self.admin.id, title="测试法规", source_type="statute",
            content="内容", status="active", version="v1",
        )
        self.db.add(source)
        self.db.commit()
        self.db.refresh(source)

        response = self.client.patch(
            f"/api/legal/sources/{source.id}/status",
            json={"status": "not_a_real_status"},
            headers=self.admin_headers,
        )
        self.assertEqual(response.status_code, 422)

    def test_update_source_status_not_found(self):
        response = self.client.patch(
            "/api/legal/sources/999999/status",
            json={"status": "inactive"},
            headers=self.admin_headers,
        )
        self.assertEqual(response.status_code, 404)

    def test_update_source_status_cannot_touch_other_users_source(self):
        source = LegalSource(
            user_id=self.member.id, title="其他用户法规", source_type="statute",
            content="内容", status="active", version="v1",
        )
        self.db.add(source)
        self.db.commit()
        self.db.refresh(source)

        response = self.client.patch(
            f"/api/legal/sources/{source.id}/status",
            json={"status": "inactive"},
            headers=self.admin_headers,
        )
        self.assertEqual(response.status_code, 404)

    # ── 检索测试工具 ──────────────────────────────────────────

    def test_retrieval_test_returns_ranked_results(self):
        source = LegalSource(
            user_id=self.admin.id, title="《劳动合同法》第40条", source_type="statute",
            citation="劳动合同法第40条", content="无过失性辞退需提前30日通知。",
            status="active", version="v1",
        )
        self.db.add(source)
        self.db.commit()

        response = self.client.post(
            "/api/legal/sources/retrieval-test",
            json={"question": "公司辞退我未提前通知"},
            headers=self.admin_headers,
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()["data"]
        self.assertIn("results", body)
        self.assertGreaterEqual(len(body["results"]), 1)
        self.assertIn("score_breakdown", body["results"][0])

    def test_retrieval_test_rejects_empty_question(self):
        response = self.client.post(
            "/api/legal/sources/retrieval-test",
            json={"question": ""},
            headers=self.admin_headers,
        )
        self.assertEqual(response.status_code, 422)

    # ── 提交审核（owner action） ──────────────────────────────

    def test_owner_can_submit_own_consultation_for_review(self):
        consultation = LegalConsultation(
            user_id=self.member.id, question="测试问题", category="other",
            known_facts_json="[]", missing_facts_json="[]", references_json="[]",
            advice="测试建议", risk_level="medium", status="pending_review",
        )
        self.db.add(consultation)
        self.db.commit()
        self.db.refresh(consultation)

        member_headers = {"Authorization": f"Bearer {create_access_token({'sub': self.member.id})}"}
        response = self.client.post(
            f"/api/legal/review-queue/consultation/{consultation.id}/actions",
            json={"action": "submit_review"},
            headers=member_headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["status"], "needs_lawyer_review")

    def test_owner_cannot_submit_others_consultation_for_review(self):
        consultation = LegalConsultation(
            user_id=self.admin.id, question="测试问题", category="other",
            known_facts_json="[]", missing_facts_json="[]", references_json="[]",
            advice="测试建议", risk_level="medium", status="pending_review",
        )
        self.db.add(consultation)
        self.db.commit()
        self.db.refresh(consultation)

        response = self.client.post(
            f"/api/legal/review-queue/consultation/{consultation.id}/actions",
            json={"action": "submit_review"},
            headers=self.member_headers,
        )
        self.assertEqual(response.status_code, 403)

    def test_owner_cannot_execute_reviewer_only_action(self):
        consultation = LegalConsultation(
            user_id=self.member.id, question="测试问题", category="other",
            known_facts_json="[]", missing_facts_json="[]", references_json="[]",
            advice="测试建议", risk_level="medium", status="pending_review",
        )
        self.db.add(consultation)
        self.db.commit()
        self.db.refresh(consultation)

        member_headers = {"Authorization": f"Bearer {create_access_token({'sub': self.member.id})}"}
        response = self.client.post(
            f"/api/legal/review-queue/consultation/{consultation.id}/actions",
            json={"action": "approve"},
            headers=member_headers,
        )
        self.assertEqual(response.status_code, 403)

    def test_reviewer_can_approve_others_consultation(self):
        consultation = LegalConsultation(
            user_id=self.member.id, question="测试问题", category="other",
            known_facts_json="[]", missing_facts_json="[]", references_json="[]",
            advice="测试建议", risk_level="medium", status="needs_lawyer_review",
        )
        self.db.add(consultation)
        self.db.commit()
        self.db.refresh(consultation)

        response = self.client.post(
            f"/api/legal/review-queue/consultation/{consultation.id}/actions",
            json={"action": "approve", "note": "已核实"},
            headers=self.admin_headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["status"], "lawyer_approved")

    # ── 审核历史与统计 ─────────────────────────────────────────

    def test_review_history_returns_actions_in_order(self):
        consultation = LegalConsultation(
            user_id=self.member.id, question="测试问题", category="other",
            known_facts_json="[]", missing_facts_json="[]", references_json="[]",
            advice="测试建议", risk_level="medium", status="needs_lawyer_review",
        )
        self.db.add(consultation)
        self.db.commit()
        self.db.refresh(consultation)

        self.client.post(
            f"/api/legal/review-queue/consultation/{consultation.id}/actions",
            json={"action": "return", "note": "缺少劳动合同证据"},
            headers=self.admin_headers,
        )

        response = self.client.get(
            f"/api/legal/review-queue/consultation/{consultation.id}/history",
            headers=self.admin_headers,
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()["data"]
        self.assertEqual(len(body["history"]), 1)
        self.assertEqual(body["history"][0]["action"], "return")
        self.assertEqual(body["history"][0]["note"], "缺少劳动合同证据")

    def test_review_history_forbidden_for_non_owner_non_reviewer(self):
        consultation = LegalConsultation(
            user_id=self.admin.id, question="测试问题", category="other",
            known_facts_json="[]", missing_facts_json="[]", references_json="[]",
            advice="测试建议", risk_level="medium", status="pending_review",
        )
        self.db.add(consultation)
        self.db.commit()
        self.db.refresh(consultation)

        response = self.client.get(
            f"/api/legal/review-queue/consultation/{consultation.id}/history",
            headers=self.member_headers,
        )
        self.assertEqual(response.status_code, 403)

    def test_review_stats_forbidden_for_non_admin(self):
        response = self.client.get("/api/legal/review-stats", headers=self.member_headers)
        self.assertEqual(response.status_code, 403)

    def test_review_stats_aggregates_return_reasons(self):
        consultation = LegalConsultation(
            user_id=self.member.id, question="测试问题", category="other",
            known_facts_json="[]", missing_facts_json="[]", references_json="[]",
            advice="测试建议", risk_level="medium", status="needs_lawyer_review",
        )
        self.db.add(consultation)
        self.db.commit()
        self.db.refresh(consultation)

        self.client.post(
            f"/api/legal/review-queue/consultation/{consultation.id}/actions",
            json={"action": "return", "note": "缺少证据材料"},
            headers=self.admin_headers,
        )

        response = self.client.get("/api/legal/review-stats", headers=self.admin_headers)
        self.assertEqual(response.status_code, 200)
        body = response.json()["data"]
        self.assertEqual(body["action_distribution"]["return"], 1)
        self.assertEqual(len(body["return_reasons"]), 1)
        self.assertEqual(body["return_reasons"][0]["note"], "缺少证据材料")

    # ── 独立批注（不改变状态） ────────────────────────────────

    def test_owner_can_add_comment_without_changing_status(self):
        consultation = LegalConsultation(
            user_id=self.member.id, question="测试问题", category="other",
            known_facts_json="[]", missing_facts_json="[]", references_json="[]",
            advice="测试建议", risk_level="medium", status="needs_lawyer_review",
        )
        self.db.add(consultation)
        self.db.commit()
        self.db.refresh(consultation)

        member_headers = {"Authorization": f"Bearer {create_access_token({'sub': self.member.id})}"}
        response = self.client.post(
            f"/api/legal/review-queue/consultation/{consultation.id}/comments",
            json={"note": "补充：已找到解除通知的书面文件"},
            headers=member_headers,
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()["data"]
        self.assertEqual(body["action"], "comment")
        self.assertEqual(body["from_status"], "needs_lawyer_review")
        self.assertEqual(body["to_status"], "needs_lawyer_review")

        # 状态本身不应改变
        detail_response = self.client.get(
            f"/api/legal/review-queue/consultation/{consultation.id}/history",
            headers=self.admin_headers,
        )
        self.assertEqual(detail_response.json()["data"]["status"], "needs_lawyer_review")

    def test_reviewer_can_add_comment(self):
        consultation = LegalConsultation(
            user_id=self.member.id, question="测试问题", category="other",
            known_facts_json="[]", missing_facts_json="[]", references_json="[]",
            advice="测试建议", risk_level="medium", status="needs_lawyer_review",
        )
        self.db.add(consultation)
        self.db.commit()
        self.db.refresh(consultation)

        response = self.client.post(
            f"/api/legal/review-queue/consultation/{consultation.id}/comments",
            json={"note": "请补充劳动合同原件扫描件"},
            headers=self.admin_headers,
        )
        self.assertEqual(response.status_code, 200)

    def test_non_owner_non_reviewer_cannot_comment(self):
        consultation = LegalConsultation(
            user_id=self.admin.id, question="测试问题", category="other",
            known_facts_json="[]", missing_facts_json="[]", references_json="[]",
            advice="测试建议", risk_level="medium", status="pending_review",
        )
        self.db.add(consultation)
        self.db.commit()
        self.db.refresh(consultation)

        response = self.client.post(
            f"/api/legal/review-queue/consultation/{consultation.id}/comments",
            json={"note": "路人留言"},
            headers=self.member_headers,
        )
        self.assertEqual(response.status_code, 403)

    def test_comment_rejects_empty_note(self):
        consultation = LegalConsultation(
            user_id=self.member.id, question="测试问题", category="other",
            known_facts_json="[]", missing_facts_json="[]", references_json="[]",
            advice="测试建议", risk_level="medium", status="pending_review",
        )
        self.db.add(consultation)
        self.db.commit()
        self.db.refresh(consultation)

        member_headers = {"Authorization": f"Bearer {create_access_token({'sub': self.member.id})}"}
        response = self.client.post(
            f"/api/legal/review-queue/consultation/{consultation.id}/comments",
            json={"note": ""},
            headers=member_headers,
        )
        self.assertEqual(response.status_code, 422)

    def test_comment_appears_in_review_stats_action_distribution(self):
        consultation = LegalConsultation(
            user_id=self.member.id, question="测试问题", category="other",
            known_facts_json="[]", missing_facts_json="[]", references_json="[]",
            advice="测试建议", risk_level="medium", status="needs_lawyer_review",
        )
        self.db.add(consultation)
        self.db.commit()
        self.db.refresh(consultation)

        self.client.post(
            f"/api/legal/review-queue/consultation/{consultation.id}/comments",
            json={"note": "留言测试"},
            headers=self.admin_headers,
        )
        stats_resp = self.client.get("/api/legal/review-stats", headers=self.admin_headers)
        self.assertEqual(stats_resp.json()["data"]["action_distribution"].get("comment"), 1)

    # ── 版本留痕：合同审查重新提交 ────────────────────────────

    def test_resubmit_contract_review_creates_version_snapshot(self):
        review = ContractReview(
            user_id=self.member.id, title="原合同", content="原始内容 违约",
            version=1, status="returned_for_facts", summary="", risks_json="[]", references_json="[]",
        )
        self.db.add(review)
        self.db.commit()
        self.db.refresh(review)

        member_headers = {"Authorization": f"Bearer {create_access_token({'sub': self.member.id})}"}
        response = self.client.post(
            f"/api/legal/contract-reviews/{review.id}/resubmit",
            json={"title": "修改后的合同", "content": "补充后的内容 违约金 争议"},
            headers=member_headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()["data"]
        self.assertEqual(body["version"], 2)
        self.assertEqual(body["title"], "修改后的合同")

        versions_resp = self.client.get(
            f"/api/legal/contract-reviews/{review.id}/versions",
            headers=member_headers,
        )
        self.assertEqual(versions_resp.status_code, 200)
        versions = versions_resp.json()["data"]
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0]["version"], 1)
        self.assertEqual(versions[0]["title"], "原合同")
        self.assertEqual(versions[0]["content"], "原始内容 违约")

    def test_resubmit_contract_review_rejects_wrong_status(self):
        review = ContractReview(
            user_id=self.member.id, title="原合同", content="原始内容",
            version=1, status="pending_review", summary="", risks_json="[]", references_json="[]",
        )
        self.db.add(review)
        self.db.commit()
        self.db.refresh(review)

        member_headers = {"Authorization": f"Bearer {create_access_token({'sub': self.member.id})}"}
        response = self.client.post(
            f"/api/legal/contract-reviews/{review.id}/resubmit",
            json={"title": "修改后的合同", "content": "补充内容"},
            headers=member_headers,
        )
        self.assertEqual(response.status_code, 400)

    # ── 版本留痕：文书草稿重新提交 ────────────────────────────

    def test_resubmit_draft_creates_version_snapshot(self):
        draft = LegalDraft(
            user_id=self.member.id, document_type="labor_arbitration_application", title="劳动争议仲裁申请书",
            fields_json=json.dumps({"申请人": "张三"}), missing_fields_json=json.dumps(["被申请人"]),
            references_json="[]", content="原始草稿内容", version=1, status="needs_facts",
        )
        self.db.add(draft)
        self.db.commit()
        self.db.refresh(draft)

        member_headers = {"Authorization": f"Bearer {create_access_token({'sub': self.member.id})}"}
        response = self.client.post(
            f"/api/legal/drafts/{draft.id}/resubmit",
            json={
                "document_type": "labor_arbitration_application",
                "fields": {
                    "申请人": "张三", "被申请人": "某公司", "仲裁请求": "支付补偿金",
                    "事实与理由": "已解除", "证据清单": "合同", "劳动关系起止时间": "2023-2026",
                },
            },
            headers=member_headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()["data"]
        self.assertEqual(body["version"], 2)
        self.assertEqual(body["status"], "pending_review")

        versions_resp = self.client.get(
            f"/api/legal/drafts/{draft.id}/versions",
            headers=member_headers,
        )
        versions = versions_resp.json()["data"]
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0]["content"], "原始草稿内容")

    def test_resubmit_draft_rejects_wrong_status(self):
        draft = LegalDraft(
            user_id=self.member.id, document_type="labor_arbitration_application", title="劳动争议仲裁申请书",
            fields_json="{}", missing_fields_json="[]", references_json="[]",
            content="内容", version=1, status="pending_review",
        )
        self.db.add(draft)
        self.db.commit()
        self.db.refresh(draft)

        member_headers = {"Authorization": f"Bearer {create_access_token({'sub': self.member.id})}"}
        response = self.client.post(
            f"/api/legal/drafts/{draft.id}/resubmit",
            json={"document_type": "labor_arbitration_application", "fields": {"申请人": "张三"}},
            headers=member_headers,
        )
        self.assertEqual(response.status_code, 400)

    # ── 单条法源创建/编辑/删除（管理后台） ─────────────────────

    def test_admin_can_create_single_source(self):
        response = self.client.post(
            "/api/legal/sources",
            json={"title": "手动创建的法源", "source_type": "statute", "content": "内容"},
            headers=self.admin_headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["title"], "手动创建的法源")

    def test_non_admin_cannot_create_source(self):
        response = self.client.post(
            "/api/legal/sources",
            json={"title": "测试", "source_type": "statute", "content": "内容"},
            headers=self.member_headers,
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_can_update_source(self):
        source = LegalSource(
            user_id=self.admin.id, title="旧标题", source_type="statute",
            content="旧内容", status="active", version="v1",
        )
        self.db.add(source)
        self.db.commit()
        self.db.refresh(source)

        response = self.client.put(
            f"/api/legal/sources/{source.id}",
            json={"title": "新标题", "source_type": "case", "content": "新内容", "status": "pending_update"},
            headers=self.admin_headers,
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()["data"]
        self.assertEqual(body["title"], "新标题")
        self.assertEqual(body["source_type"], "case")
        self.assertEqual(body["status"], "pending_update")

    def test_admin_can_delete_source(self):
        source = LegalSource(
            user_id=self.admin.id, title="待删除", source_type="statute",
            content="内容", status="active", version="v1",
        )
        self.db.add(source)
        self.db.commit()
        self.db.refresh(source)

        response = self.client.delete(f"/api/legal/sources/{source.id}", headers=self.admin_headers)
        self.assertEqual(response.status_code, 200)

        # 直接查库确认已删除，不依赖 list 接口（会触发 ensure_demo_sources 插入新数据，
        # SQLite 可能复用被删除的 rowid，用 ID 判断会有假阳性风险）
        remaining = self.db.query(LegalSource).filter(LegalSource.id == source.id).first()
        self.assertIsNone(remaining)

    def test_delete_source_not_found(self):
        response = self.client.delete("/api/legal/sources/999999", headers=self.admin_headers)
        self.assertEqual(response.status_code, 404)

    def test_invalid_action_rejected(self):
        consultation = LegalConsultation(
            user_id=self.member.id, question="测试问题", category="other",
            known_facts_json="[]", missing_facts_json="[]", references_json="[]",
            advice="测试建议", risk_level="medium", status="pending_review",
        )
        self.db.add(consultation)
        self.db.commit()
        self.db.refresh(consultation)

        member_headers = {"Authorization": f"Bearer {create_access_token({'sub': self.member.id})}"}
        response = self.client.post(
            f"/api/legal/review-queue/consultation/{consultation.id}/actions",
            json={"action": "not_a_real_action"},
            headers=member_headers,
        )
        self.assertEqual(response.status_code, 400)


    # ── 条文级检索（Phase 7-3） ──────────────────────────────

    def test_list_articles_returns_source_articles(self):
        source = LegalSource(user_id=self.admin.id, title="《测试法》", source_type="statute", content="测试内容", status="active", version="v1")
        self.db.add(source)
        self.db.flush()
        self.db.add(LegalArticle(source_id=source.id, article_number="第1条", content="本法适用于所有测试场景。", sequence=1))
        self.db.add(LegalArticle(source_id=source.id, article_number="第2条", content="测试应当被鼓励。", sequence=2))
        self.db.commit()

        response = self.client.get(f"/api/legal/sources/{source.id}/articles", headers=self.admin_headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["article_number"], "第1条")

    def test_search_articles_by_keyword(self):
        source = LegalSource(user_id=self.admin.id, title="《测试法》", source_type="statute", content="测试", status="active", version="v1")
        self.db.add(source)
        self.db.flush()
        self.db.add(LegalArticle(source_id=source.id, article_number="第1条", content="关于劳动合同解除的具体规定", sequence=1))
        self.db.add(LegalArticle(source_id=source.id, article_number="第2条", content="经济补偿金的计算标准", sequence=2))
        self.db.commit()

        response = self.client.get("/api/legal/article-search?q=经济补偿", headers=self.admin_headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertGreaterEqual(len(data), 1)
        self.assertIn("经济补偿金", data[0]["content"])

    def test_hybrid_retrieval_test_returns_article_results(self):
        source = LegalSource(user_id=self.admin.id, title="《劳动法》", source_type="statute", content="劳动合同", status="active", version="v1")
        self.db.add(source)
        self.db.flush()
        self.db.add(LegalArticle(source_id=source.id, article_number="第40条", content="无过失性辞退相关规定", sequence=1))
        self.db.commit()

        response = self.client.post(
            "/api/legal/sources/hybrid-retrieval-test",
            json={"question": "无过失性辞退条件"},
            headers=self.admin_headers,
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertIn("results", data)
        self.assertIn("total_articles", data)
        self.assertGreaterEqual(data["total_articles"], 1)
        result = data["results"][0]
        for key in ("id", "source_id", "article_number", "content", "score"):
            self.assertIn(key, result)

    def test_source_relations_empty_when_no_links(self):
        source = LegalSource(user_id=self.admin.id, title="《测试法》", source_type="statute", content="test", status="active", version="v1")
        self.db.add(source)
        self.db.commit()
        self.db.refresh(source)

        response = self.client.get(f"/api/legal/sources/{source.id}/relations", headers=self.admin_headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["amended_by"], [])
        self.assertEqual(data["amends"], [])
        self.assertEqual(data["source_id"], source.id)

    def test_source_relations_resolves_linked_sources(self):
        old_law = LegalSource(
            user_id=self.admin.id, title="《劳动法》", source_type="statute",
            content="旧版", status="inactive", version="v1",
        )
        import json
        new_law = LegalSource(
            user_id=self.admin.id, title="《劳动合同法》", source_type="statute",
            content="新版", status="active", version="v1",
        )
        self.db.add_all([old_law, new_law])
        self.db.flush()
        # new_law amends old_law
        new_law.amends_json = json.dumps([old_law.id])
        old_law.amended_by_json = json.dumps([new_law.id])
        self.db.commit()
        self.db.refresh(new_law)

        response = self.client.get(f"/api/legal/sources/{new_law.id}/relations", headers=self.admin_headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(len(data["amends"]), 1)
        self.assertEqual(data["amends"][0]["id"], old_law.id)
        self.assertEqual(data["amended_by"], [])

    def test_source_relations_not_found(self):
        response = self.client.get("/api/legal/sources/999999/relations", headers=self.admin_headers)
        self.assertEqual(response.status_code, 404)
