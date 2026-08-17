"""P-1 试点漏斗：/api/dashboard/funnel 从既有业务表推导用户转化漏斗"""
import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import create_access_token, hash_password
from app.core.database import Base, get_db
from app.main import app
from app.models.user import User, UserStatus
from app.models.legal import LegalConsultation, ContractReview, LegalDraft
from app.models.subscription import SubscriptionPlan, UserSubscription, SubscriptionStatus
from app.services.billing.subscription_service import subscription_service


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FunnelDashboardTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session()
        subscription_service.ensure_default_plans(self.db)

        now = _utcnow()

        def _user(name, email, role="user", created_at=None):
            u = User(
                username=name, email=email,
                hashed_password=hash_password("pw"), role=role,
                status=UserStatus.active.value,
                created_at=created_at or now,
            )
            self.db.add(u)
            return u

        self.admin = _user("admin", "a@t.com", role="admin", created_at=now)
        self.u1 = _user("u1", "u1@t.com", created_at=now - timedelta(days=3))   # 咨询+审核通过
        self.u2 = _user("u2", "u2@t.com", created_at=now - timedelta(days=3))   # 咨询+文书+升级
        self.u3 = _user("u3", "u3@t.com", created_at=now - timedelta(days=1))   # 仅审查
        self.old = _user("old", "old@t.com", created_at=now - timedelta(days=60))  # 队列外
        self.db.commit()
        self.db.refresh(self.admin)

        # u1：3天前咨询，2天前被律师审核通过（已采纳），另有一份待审合同
        c1 = LegalConsultation(user_id=self.u1.id, category="labor", question="q1", advice="a1")
        c1.created_at = now - timedelta(days=2)
        self.db.add(c1)
        c1_rev = ContractReview(user_id=self.u1.id, title="c1", content="合同1", status="lawyer_approved")
        c1_rev.created_at = now - timedelta(days=2)
        self.db.add(c1_rev)
        self.db.add(ContractReview(user_id=self.u1.id, title="c2", content="合同2"))

        # u2：3天前咨询，2天前文书，已升级 pro
        c2 = LegalConsultation(user_id=self.u2.id, category="labor", question="q2", advice="a2")
        c2.created_at = now - timedelta(days=3)
        self.db.add(c2)
        d2 = LegalDraft(user_id=self.u2.id, document_type="labor_arbitration_application", title="申请书")
        d2.created_at = now - timedelta(days=2)
        self.db.add(d2)

        # u3：仅一份待审合同
        self.db.add(ContractReview(user_id=self.u3.id, title="c3", content="合同3"))

        # old（队列外）：有咨询，不应计入漏斗
        c_old = LegalConsultation(user_id=self.old.id, category="labor", question="q_old", advice="a_old")
        c_old.created_at = now - timedelta(days=30)
        self.db.add(c_old)

        # admin（排除）：有咨询，不应计入漏斗
        c_admin = LegalConsultation(user_id=self.admin.id, category="labor", question="q_adm", advice="a_adm")
        c_admin.created_at = now - timedelta(days=2)
        self.db.add(c_admin)

        pro_plan = self.db.query(SubscriptionPlan).filter(SubscriptionPlan.tier == "pro").first()
        self.db.add(UserSubscription(
            user_id=self.u2.id, plan_id=pro_plan.id,
            status=SubscriptionStatus.active.value,
        ))
        self.db.commit()

        app.dependency_overrides[get_db] = lambda: self.db
        token = create_access_token({"sub": str(self.admin.id)})
        self.client = TestClient(app, raise_server_exceptions=False)
        self.headers = {"Authorization": f"Bearer {token}"}

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def _get_funnel(self, days=30):
        resp = self.client.get(f"/api/admin/funnel?days={days}", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        return resp.json()["data"]

    def test_cohort_excludes_admin_and_outside_window(self):
        data = self._get_funnel()
        self.assertEqual(data["cohort"]["registered"], 3)  # u1/u2/u3，admin 与 60 天前 old 排除
        self.assertEqual(data["cohort"]["scope"], "role != admin")

    def test_funnel_stage_counts(self):
        data = self._get_funnel()
        counts = {row["stage"]: row["users"] for row in data["funnel"]}
        self.assertEqual(counts["registered"], 3)
        self.assertEqual(counts["first_consultation"], 2)       # u1, u2（old/admin 不计）
        self.assertEqual(counts["first_contract_review"], 2)    # u1, u3
        self.assertEqual(counts["first_draft"], 1)              # u2
        self.assertEqual(counts["first_review_approved"], 1)    # u1（c1 被律师通过）
        self.assertEqual(counts["upgraded"], 1)                 # u2 pro

    def test_funnel_rates(self):
        data = self._get_funnel()
        rows = {row["stage"]: row for row in data["funnel"]}
        self.assertEqual(rows["registered"]["overall_rate"], 1.0)
        self.assertEqual(rows["first_consultation"]["overall_rate"], round(2 / 3, 4))
        self.assertEqual(rows["first_consultation"]["hop_rate"], round(2 / 3, 4))
        self.assertEqual(rows["upgraded"]["hop_rate"], round(1 / 1, 4))
        # 注册阶段无上一跳，hop 与 overall 一致
        self.assertEqual(rows["registered"]["hop_rate"], 1.0)

    def test_activation_avg_days(self):
        data = self._get_funnel()
        # u1 注册后 1 天首次咨询，u2 当天咨询 → 平均 0.5 天
        self.assertEqual(data["activation"]["avg_days_reg_to_first_consult"], 0.5)
        self.assertEqual(data["activation"]["cohort_users_with_consultation"], 2)

    def test_empty_database(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        db = Session()
        admin = User(
            username="admin", email="a@t.com",
            hashed_password=hash_password("pw"), role="admin",
            status=UserStatus.active.value, created_at=_utcnow(),
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)

        app.dependency_overrides[get_db] = lambda: db
        token = create_access_token({"sub": str(admin.id)})
        client = TestClient(app, raise_server_exceptions=False)
        try:
            resp = client.get("/api/admin/funnel?days=30", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(resp.status_code, 200)
            data = resp.json()["data"]
            self.assertEqual(data["cohort"]["registered"], 0)
            for row in data["funnel"]:
                self.assertEqual(row["users"], 0)
                self.assertEqual(row["overall_rate"], 0.0)
            self.assertIsNone(data["activation"]["avg_days_reg_to_first_consult"])
        finally:
            app.dependency_overrides.clear()
            db.close()

    def test_non_admin_forbidden(self):
        token = create_access_token({"sub": str(self.u1.id)})
        resp = self.client.get(
            "/api/admin/funnel?days=30",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertIn("ADMIN_REQUIRED", resp.text)

    def test_legal_billing_fallback_without_subscription_tables(self):
        """无订阅表的库（生产库为 legal 计费）应走组织收款口径，且不 500"""
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        db = Session()

        now = _utcnow()
        admin = User(
            username="admin", email="a@t.com",
            hashed_password=hash_password("pw"), role="admin",
            status=UserStatus.active.value, created_at=now,
        )
        db.add(admin)
        paid = User(
            username="paid", email="paid@t.com",
            hashed_password=hash_password("pw"), role="user",
            status=UserStatus.active.value, created_at=now - timedelta(days=3),
            organization_id=1,
        )
        db.add(paid)
        unpaid = User(
            username="unpaid", email="u@t.com",
            hashed_password=hash_password("pw"), role="user",
            status=UserStatus.active.value, created_at=now - timedelta(days=2),
            organization_id=2,
        )
        db.add(unpaid)
        db.commit()
        db.refresh(admin)

        # 造一张已确认收款的发票与关联支付记录（组织 1 已付款）
        from app.models.legal_billing import LegalInvoice, LegalPaymentRecord
        inv = LegalInvoice(
            organization_id=1, case_id=1, invoice_no="INV-001",
            client_display_name="客户", issue_date=now.date(),
            total_amount=100, status="paid", created_by=paid.id,
        )
        db.add(inv)
        db.commit()
        db.refresh(inv)
        db.add(LegalPaymentRecord(
            invoice_id=inv.id, organization_id=1, amount=100,
            payment_method="bank_transfer", status="confirmed", recorded_by=paid.id,
        ))
        db.commit()

        # 删除订阅表，模拟生产库结构
        db.execute(__import__("sqlalchemy").text("DROP TABLE user_subscriptions"))
        db.execute(__import__("sqlalchemy").text("DROP TABLE subscription_plans"))
        db.commit()

        app.dependency_overrides[get_db] = lambda: db
        token = create_access_token({"sub": str(admin.id)})
        client = TestClient(app, raise_server_exceptions=False)
        try:
            resp = client.get("/api/admin/funnel?days=30", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(resp.status_code, 200)
            data = resp.json()["data"]
            self.assertEqual(data["cohort"]["billing_source"], "legal_billing")
            counts = {row["stage"]: row["users"] for row in data["funnel"]}
            self.assertEqual(counts["registered"], 2)
            self.assertEqual(counts["upgraded"], 1)  # paid 所在组织 1 已收款
        finally:
            app.dependency_overrides.clear()
            db.close()


if __name__ == "__main__":
    unittest.main()
