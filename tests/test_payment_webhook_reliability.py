"""支付 Webhook 可靠性测试：幂等、乱序、安全拒绝、退款去重与超额。"""
import hashlib
import hmac
import json
import time as _time
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import hash_password
from app.core.config import get_settings
from app.core.database import Base
from app.models.payment_event import PaymentEvent
from app.models.subscription import SubscriptionStatus, UserSubscription
from app.models.user import User, UserStatus
from app.services.billing.payment_event_service import (
    WebhookRejectedError, payment_event_service,
)
from app.services.billing.subscription_service import subscription_service


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


def _stripe_sub_payload(user_id, sub_id="sub_1", *, created=None, plan="pro",
                        event_type="customer.subscription.created", customer="cus_1"):
    return {
        "id": f"evt_{sub_id}",
        "created": created if created is not None else int(_time.time()),
        "provider": "stripe",
        "event_type": event_type,
        "data": {"object": {
            "id": sub_id, "customer": customer,
            "metadata": {"user_id": str(user_id), "plan_tier": plan},
        }},
    }


class PaymentWebhookReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)()
        self.user = User(username="w", email="w@x.com", hashed_password=hash_password("pw"),
                         role="user", status=UserStatus.active.value)
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)
        subscription_service.ensure_default_plans(self.db)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    # ── 幂等：同一 provider_event_id 只处理一次（acceptance 3）────────

    def test_same_event_id_processed_once(self):
        payload = _stripe_sub_payload(self.user.id, sub_id="sub_dup")
        e1 = payment_event_service.record_event(
            db=self.db, provider="stripe", provider_event_id="evt_sub_dup",
            event_type="customer.subscription.created", raw_payload=payload)
        e2 = payment_event_service.record_event(
            db=self.db, provider="stripe", provider_event_id="evt_sub_dup",
            event_type="customer.subscription.created", raw_payload=payload)
        self.assertEqual(e1.id, e2.id, "重复回调返回既有事件")
        self.assertEqual(self.db.query(PaymentEvent).count(), 1)
        # 处理一次 → 激活；重放 → no-op
        self.assertEqual(payment_event_service.process_event(self.db, e1), "completed")
        self.assertEqual(payment_event_service.process_event(self.db, e1), "completed")
        active = subscription_service.get_active_subscription(self.db, self.user.id)
        self.assertIsNotNone(active)
        self.assertEqual(self.db.query(UserSubscription).filter(
            UserSubscription.status == SubscriptionStatus.active.value).count(), 1)

    # ── 乱序：旧 deleted 不覆盖新 created（acceptance 4）──────────────

    def test_out_of_order_old_deleted_does_not_revert(self):
        now = int(_time.time())
        created_payload = _stripe_sub_payload(self.user.id, sub_id="sub_ord",
                                              created=now, event_type="customer.subscription.created")
        ev_created = payment_event_service.record_event(
            db=self.db, provider="stripe", provider_event_id="evt_ord_created",
            event_type="customer.subscription.created", raw_payload=created_payload)
        payment_event_service.process_event(self.db, ev_created)
        self.assertIsNotNone(subscription_service.get_active_subscription(self.db, self.user.id))

        # 后到达的旧 deleted（occurred_at 更早）→ 不得覆盖
        old_deleted = _stripe_sub_payload(self.user.id, sub_id="sub_ord",
                                          created=now - 1000,
                                          event_type="customer.subscription.deleted")
        ev_old = payment_event_service.record_event(
            db=self.db, provider="stripe", provider_event_id="evt_ord_old_deleted",
            event_type="customer.subscription.deleted", raw_payload=old_deleted)
        payment_event_service.process_event(self.db, ev_old)
        self.assertIsNotNone(subscription_service.get_active_subscription(self.db, self.user.id),
                             "旧 deleted 事件不得使订阅回退")

        # 新的 deleted → 取消
        new_deleted = _stripe_sub_payload(self.user.id, sub_id="sub_ord",
                                          created=now + 100,
                                          event_type="customer.subscription.deleted")
        ev_new = payment_event_service.record_event(
            db=self.db, provider="stripe", provider_event_id="evt_ord_new_deleted",
            event_type="customer.subscription.deleted", raw_payload=new_deleted)
        payment_event_service.process_event(self.db, ev_new)
        self.assertIsNone(subscription_service.get_active_subscription(self.db, self.user.id))

    # ── 安全拒绝：签名/时间戳/未知映射（acceptance 5）─────────────────

    def test_bad_signature_rejected_no_side_effects(self):
        with patch.object(get_settings(), "PAYMENT_WEBHOOK_SECRET", "whsec_x"):
            raw = json.dumps(_stripe_sub_payload(self.user.id)).encode("utf-8")
            ts = str(int(_time.time()))  # 当前时间戳，仅签名错误
            with self.assertRaises(WebhookRejectedError) as ctx:
                payment_event_service.verify_signature(
                    raw, f"t={ts},v1=wrong", require=True)
            self.assertEqual(ctx.exception.code, "INVALID_WEBHOOK_SIGNATURE")
        self.assertEqual(self.db.query(PaymentEvent).count(), 0)
        self.assertIsNone(subscription_service.get_active_subscription(self.db, self.user.id))

    def test_expired_signature_rejected(self):
        with patch.object(get_settings(), "PAYMENT_WEBHOOK_SECRET", "whsec_x"):
            raw = json.dumps(_stripe_sub_payload(self.user.id)).encode("utf-8")
            ts = str(int(_time.time()) - 100000)
            sig = hmac.new(b"whsec_x", f"{ts}.{raw.decode('utf-8')}".encode("utf-8"),
                           hashlib.sha256).hexdigest()
            with self.assertRaises(WebhookRejectedError) as ctx:
                payment_event_service.verify_signature(raw, f"t={ts},v1={sig}", require=True)
            self.assertEqual(ctx.exception.code, "WEBHOOK_SIGNATURE_EXPIRED")

    def test_fail_closed_when_no_secret(self):
        with patch.object(get_settings(), "PAYMENT_WEBHOOK_SECRET", ""):
            with self.assertRaises(WebhookRejectedError) as ctx:
                payment_event_service.verify_signature(b"{}", "t=1,v1=x", require=True)
            self.assertEqual(ctx.exception.code, "WEBHOOK_SIGNATURE_NOT_CONFIGURED")

    def test_unknown_mapping_goes_needs_reconciliation(self):
        payload = _stripe_sub_payload(999999, sub_id="sub_unknown")  # 不存在的用户
        ev = payment_event_service.record_event(
            db=self.db, provider="stripe", provider_event_id="evt_unknown",
            event_type="customer.subscription.created", raw_payload=payload)
        status = payment_event_service.process_event(self.db, ev)
        self.assertEqual(status, "needs_reconciliation")
        self.assertIsNone(subscription_service.get_active_subscription(self.db, self.user.id),
                          "未知归属不得授予订阅")

    # ── 支付成功/退款重复（acceptance 6 部分）────────────────────────

    def test_refund_duplicate_and_over(self):
        from app.models.legal import LegalCase
        from app.models.legal_billing import LegalInvoice
        from app.services.billing.billing_service import billing_service

        case = LegalCase(organization_id=1, user_id=self.user.id, title="c", case_type="other")
        self.db.add(case)
        self.db.flush()
        invoice = LegalInvoice(organization_id=1, case_id=case.id, invoice_no="INV-1",
                               client_display_name="X", issue_date=date(2026, 8, 1),
                               subtotal=100, tax_amount=0, discount_amount=0,
                               total_amount=100, status="sent", payment_progress="unpaid",
                               currency="CNY", created_by=self.user.id)
        self.db.add(invoice)
        self.db.commit()
        self.db.refresh(invoice)
        pay = billing_service.record_payment(
            db=self.db, invoice_id=invoice.id, organization_id=1, amount=Decimal("100"),
            payment_method="provider", recorded_by=self.user.id, transaction_id="tx-1")
        # 重复支付（同 transaction_id）→ 返回既有，不入账两次
        pay2 = billing_service.record_payment(
            db=self.db, invoice_id=invoice.id, organization_id=1, amount=Decimal("100"),
            payment_method="provider", recorded_by=self.user.id, transaction_id="tx-1")
        self.assertEqual(pay.id, pay2.id)
        # 超额退款拒绝
        refund = billing_service.request_refund(
            db=self.db, invoice_id=invoice.id, payment_record_id=pay.id, organization_id=1,
            amount=Decimal("30"), reason="部分退款", recorded_by=self.user.id)
        billing_service.approve_refund(db=self.db, refund_id=refund.id, approved_by=self.user.id,
                                       approved=True)
        self.assertEqual(refund.status, "completed")
        with self.assertRaises(ValueError):
            billing_service.request_refund(
                db=self.db, invoice_id=invoice.id, payment_record_id=pay.id, organization_id=1,
                amount=Decimal("80"), reason="超额", recorded_by=self.user.id)


if __name__ == "__main__":
    unittest.main()
