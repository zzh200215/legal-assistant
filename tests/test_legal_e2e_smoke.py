"""端到端闭环冒烟测试：串联 Phase 2/3/4 新增能力，验证三阶段叠加后无冲突。

场景：法源批量导入 -> 检索测试 -> 法律咨询 -> 提交审核 -> 律师审核通过
     -> 合同审查 -> 上传合同文件 -> 律师退回 -> 审核历史/统计核对
"""
import io
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import create_access_token, hash_password
from app.core.database import Base, get_db
from app.main import app
from app.models.user import User


class LegalEndToEndSmokeTest(unittest.TestCase):
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

        self.lawyer = User(
            username="e2e_lawyer", email="e2e_lawyer@example.com",
            hashed_password=hash_password("secret"), role="admin",
        )
        self.client_user = User(
            username="e2e_client", email="e2e_client@example.com",
            hashed_password=hash_password("secret"), role="user",
        )
        self.db.add_all([self.lawyer, self.client_user])
        self.db.commit()
        self.db.refresh(self.lawyer)
        self.db.refresh(self.client_user)

        def override_get_db():
            db = self.TestingSessionLocal()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)
        self.lawyer_headers = {"Authorization": f"Bearer {create_access_token({'sub': self.lawyer.id})}"}
        self.client_headers = {"Authorization": f"Bearer {create_access_token({'sub': self.client_user.id})}"}

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def test_full_legal_workflow_across_phase_2_3_4(self):
        # ── Phase 3: 法源批量导入（律师身份，管理员权限） ──
        csv_content = (
            "title,source_type,content,citation,version,status\n"
            "《劳动合同法》第40条,statute,无过失性辞退需提前30日通知或额外支付一个月工资。,劳动合同法第40条,v1,active\n"
            "《劳动合同法》第47条,statute,经济补偿按工作年限每满一年支付一个月工资。,劳动合同法第47条,v1,active\n"
        )
        files = {"file": ("sources.csv", io.BytesIO(csv_content.encode("utf-8-sig")), "text/csv")}
        import_resp = self.client.post("/api/legal/sources/import", files=files, headers=self.lawyer_headers)
        self.assertEqual(import_resp.status_code, 200, import_resp.text)
        self.assertEqual(import_resp.json()["data"]["imported"], 2)

        # 客户方也需要能看到法源（法源按用户隔离，客户首次访问会触发 ensure_demo_sources）
        # ── Phase 3: 检索测试工具（律师验证召回效果） ──
        retrieval_resp = self.client.post(
            "/api/legal/sources/retrieval-test",
            json={"question": "公司未提前通知辞退我，应支付多少经济补偿？"},
            headers=self.lawyer_headers,
        )
        self.assertEqual(retrieval_resp.status_code, 200)
        retrieval_data = retrieval_resp.json()["data"]
        self.assertGreaterEqual(len(retrieval_data["results"]), 1)
        top_result = retrieval_data["results"][0]
        self.assertGreater(top_result["total_score"], 0)

        # ── Phase 1/2: 客户提交法律咨询 ──
        consult_resp = self.client.post(
            "/api/legal/consultations",
            json={"question": "公司以业务调整为由单方解除劳动合同，未提前30日通知，也未支付经济补偿金，我该怎么办？"},
            headers=self.client_headers,
        )
        self.assertEqual(consult_resp.status_code, 200, consult_resp.text)
        consultation = consult_resp.json()["data"]
        self.assertIn(consultation["status"], {"pending_review", "needs_lawyer_review"})
        consultation_id = consultation["id"]

        # ── Phase 2: 客户点击"提交律师审核"按钮 ──
        submit_resp = self.client.post(
            f"/api/legal/review-queue/consultation/{consultation_id}/actions",
            json={"action": "submit_review"},
            headers=self.client_headers,
        )
        self.assertEqual(submit_resp.status_code, 200, submit_resp.text)
        self.assertEqual(submit_resp.json()["data"]["status"], "needs_lawyer_review")

        # 客户本人不能越权执行审核律师专属动作
        forbidden_resp = self.client.post(
            f"/api/legal/review-queue/consultation/{consultation_id}/actions",
            json={"action": "approve"},
            headers=self.client_headers,
        )
        self.assertEqual(forbidden_resp.status_code, 403)

        # ── Phase 1: 审核队列应能看到这条咨询 ──
        queue_resp = self.client.get("/api/legal/review-queue", headers=self.lawyer_headers)
        self.assertEqual(queue_resp.status_code, 200)
        queue_items = queue_resp.json()["data"]
        matched = [item for item in queue_items if item["target_type"] == "consultation" and item["id"] == consultation_id]
        self.assertEqual(len(matched), 1)

        # ── Phase 4: 律师审核通过 ──
        approve_resp = self.client.post(
            f"/api/legal/review-queue/consultation/{consultation_id}/actions",
            json={"action": "approve", "note": "已核实工作年限与工资标准，建议按此金额协商"},
            headers=self.lawyer_headers,
        )
        self.assertEqual(approve_resp.status_code, 200)
        self.assertEqual(approve_resp.json()["data"]["status"], "lawyer_approved")

        # ── Phase 4: 审核历史应记录完整链路（提交 + 通过） ──
        history_resp = self.client.get(
            f"/api/legal/review-queue/consultation/{consultation_id}/history",
            headers=self.client_headers,
        )
        self.assertEqual(history_resp.status_code, 200)
        history = history_resp.json()["data"]["history"]
        self.assertEqual(len(history), 2)
        actions_seen = {h["action"] for h in history}
        self.assertEqual(actions_seen, {"submit_review", "approve"})

        # ── Phase 2: 合同审查完整流程 ──
        contract_content = (
            "甲方：星河制造 乙方：云帆智能\n"
            "合同总金额为268万元。\n"
            "付款方式：签约后5个工作日内付100万，阶段验收后付108万，最终验收后付60万。\n"
            "违约责任：逾期交付按日0.3%支付违约金，上限为合同总额10%。\n"
        )
        review_resp = self.client.post(
            "/api/legal/contract-reviews",
            json={"title": "技术服务合同", "content": contract_content},
            headers=self.client_headers,
        )
        self.assertEqual(review_resp.status_code, 200, review_resp.text)
        contract_review = review_resp.json()["data"]
        self.assertGreater(len(contract_review["risks"]), 0)
        contract_review_id = contract_review["id"]

        # ── Phase 4: 律师退回合同审查，要求补充证据 ──
        return_resp = self.client.post(
            f"/api/legal/review-queue/contract_review/{contract_review_id}/actions",
            json={"action": "return", "note": "请补充验收标准的书面约定"},
            headers=self.lawyer_headers,
        )
        self.assertEqual(return_resp.status_code, 200)
        self.assertEqual(return_resp.json()["data"]["status"], "returned_for_facts")

        # ── Phase 4: 审核统计应正确聚合本轮全部动作 ──
        stats_resp = self.client.get("/api/legal/review-stats", headers=self.lawyer_headers)
        self.assertEqual(stats_resp.status_code, 200)
        stats = stats_resp.json()["data"]
        self.assertEqual(stats["action_distribution"].get("submit_review"), 1)
        self.assertEqual(stats["action_distribution"].get("approve"), 1)
        self.assertEqual(stats["action_distribution"].get("return"), 1)
        return_notes = [r["note"] for r in stats["return_reasons"]]
        self.assertIn("请补充验收标准的书面约定", return_notes)

        # ── Phase 1: 运营看板指标应可正常聚合，不因新流程崩溃 ──
        metrics_resp = self.client.get("/api/legal/metrics", headers=self.client_headers)
        self.assertEqual(metrics_resp.status_code, 200)
        metrics = metrics_resp.json()["data"]
        self.assertEqual(metrics["totals"]["consultations"], 1)
        self.assertEqual(metrics["totals"]["contract_reviews"], 1)


if __name__ == "__main__":
    unittest.main()
