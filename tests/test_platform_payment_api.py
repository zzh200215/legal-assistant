"""#83/平台收款（对公转账）API 回归测试"""
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import hash_password, create_access_token
from app.core.database import Base, get_db
from app.main import app
from app.models.user import User, UserStatus
from app.models.org import Organization, OrganizationMember
from app.models.platform_payment import PlatformPayment
from app.models.subscription import UserSubscription, SubscriptionStatus
from app.services.subscription_service import subscription_service


def _make_engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


class PlatformPaymentApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session()
        subscription_service.ensure_default_plans(self.db)

        org = Organization(name="转账企业", code="BT1")
        self.db.add(org)
        self.db.flush()
        self.org_id = org.id

        self.user = User(
            username="payer1", email="payer1@t.com",
            hashed_password=hash_password("pw"), role="user",
            status=UserStatus.active.value,
        )
        self.db.add(self.user)
        self.db.flush()
        self.db.add(OrganizationMember(organization_id=org.id, user_id=self.user.id, legal_role="admin"))

        self.admin = User(
            username="padmin", email="padmin@t.com",
            hashed_password=hash_password("pw"), role="admin",
            status=UserStatus.active.value,
        )
        self.db.add(self.admin)
        self.db.commit()

        self.user_token = create_access_token({"sub": str(self.user.id)})
        self.admin_token = create_access_token({"sub": str(self.admin.id)})

        def _override_db():
            db = Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = _override_db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.pop(get_db, None)
        self.db.close()
        self.engine.dispose()

    def _headers(self, token):
        return {"Authorization": f"Bearer {token}"}

    def test_submit_bank_transfer(self):
        r = self.client.post(
            "/api/billing/payments/bank-transfer",
            json={"plan_tier": "team", "amount": 999, "voucher_no": "V20260901"},
            headers=self._headers(self.user_token),
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["data"]["status"], "pending")

    def test_submit_amount_mismatch(self):
        r = self.client.post(
            "/api/billing/payments/bank-transfer",
            json={"plan_tier": "team", "amount": 100},
            headers=self._headers(self.user_token),
        )
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("金额", r.text)

    def test_submit_requires_org_member(self):
        orphan = User(
            username="orphan1", email="o1@t.com",
            hashed_password=hash_password("pw"), role="user",
            status=UserStatus.active.value,
        )
        self.db.add(orphan)
        self.db.commit()
        token = create_access_token({"sub": str(orphan.id)})
        r = self.client.post(
            "/api/billing/payments/bank-transfer",
            json={"plan_tier": "pro", "amount": 199},
            headers=self._headers(token),
        )
        self.assertEqual(r.status_code, 403, r.text)

    def test_confirm_activates_subscription(self):
        self.client.post(
            "/api/billing/payments/bank-transfer",
            json={"plan_tier": "pro", "amount": 199, "voucher_no": "V2"},
            headers=self._headers(self.user_token),
        )
        payment_id = self.db.query(PlatformPayment).first().id

        r = self.client.post(
            f"/api/billing/payments/{payment_id}/confirm",
            json={"invoice_snapshot": {"title": "某某律所", "tax_no": "91330100MA123"}},
            headers=self._headers(self.admin_token),
        )
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()["data"]
        self.assertEqual(data["status"], "confirmed")

        payment = self.db.query(PlatformPayment).get(payment_id)
        self.assertIn("91330100MA123", payment.invoice_snapshot_json)
        sub = (
            self.db.query(UserSubscription)
            .filter(UserSubscription.user_id == self.user.id)
            .order_by(UserSubscription.id.desc())
            .first()
        )
        self.assertIsNotNone(sub)
        self.assertEqual(sub.status, SubscriptionStatus.active.value)
        self.assertEqual(sub.payment_provider, "bank_transfer")

    def test_confirm_non_admin_forbidden(self):
        self.client.post(
            "/api/billing/payments/bank-transfer",
            json={"plan_tier": "pro", "amount": 199},
            headers=self._headers(self.user_token),
        )
        payment_id = self.db.query(PlatformPayment).first().id
        r = self.client.post(
            f"/api/billing/payments/{payment_id}/confirm",
            json={},
            headers=self._headers(self.user_token),
        )
        self.assertEqual(r.status_code, 403, r.text)

    def test_reject_flow(self):
        self.client.post(
            "/api/billing/payments/bank-transfer",
            json={"plan_tier": "pro", "amount": 199},
            headers=self._headers(self.user_token),
        )
        payment_id = self.db.query(PlatformPayment).first().id
        r = self.client.post(
            f"/api/billing/payments/{payment_id}/reject",
            json={"note": "凭证不符"},
            headers=self._headers(self.admin_token),
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.db.query(PlatformPayment).get(payment_id).status, "rejected")

    def test_double_confirm_rejected(self):
        self.client.post(
            "/api/billing/payments/bank-transfer",
            json={"plan_tier": "pro", "amount": 199},
            headers=self._headers(self.user_token),
        )
        payment_id = self.db.query(PlatformPayment).first().id
        self.client.post(f"/api/billing/payments/{payment_id}/confirm", json={}, headers=self._headers(self.admin_token))
        r = self.client.post(f"/api/billing/payments/{payment_id}/confirm", json={}, headers=self._headers(self.admin_token))
        self.assertEqual(r.status_code, 400, r.text)


if __name__ == "__main__":
    unittest.main()
