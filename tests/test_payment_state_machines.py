"""统一计费/订阅状态机测试：拒绝非法跳转。"""
import unittest

from app.services.billing.billing_state_machines import (
    BillingStateError, subscription_transition, platform_payment_transition,
    payment_record_transition, invoice_transition, refund_transition, reservation_transition,
)


class BillingStateMachineTests(unittest.TestCase):
    def test_subscription_rejects_illegal(self):
        # 合法
        subscription_transition("pending", "active")
        subscription_transition("active", "past_due")
        subscription_transition("past_due", "suspended")
        subscription_transition("suspended", "active")
        subscription_transition("active", "cancelled")
        subscription_transition("active", "expired")
        subscription_transition("trialing", "active")
        # 非法
        for bad in (("cancelled", "active"), ("expired", "active"), ("cancelled", "expired"),
                    ("active", "trialing"), ("pending", "past_due")):
            with self.assertRaises(BillingStateError, msg=f"{bad}"):
                subscription_transition(*bad)

    def test_platform_payment_rejects_illegal(self):
        platform_payment_transition("pending", "confirmed")
        platform_payment_transition("pending", "rejected")
        platform_payment_transition("confirmed", "refunded")
        for bad in (("confirmed", "rejected"), ("rejected", "confirmed"), ("refunded", "confirmed"),
                    ("pending", "refunded")):
            with self.assertRaises(BillingStateError):
                platform_payment_transition(*bad)

    def test_payment_record_rejects_illegal(self):
        payment_record_transition("confirmed", "refunded")
        payment_record_transition("confirmed", "disputed")
        with self.assertRaises(BillingStateError):
            payment_record_transition("refunded", "confirmed")
        with self.assertRaises(BillingStateError):
            payment_record_transition("disputed", "confirmed")

    def test_invoice_rejects_illegal(self):
        invoice_transition("draft", "sent")
        invoice_transition("draft", "voided")
        invoice_transition("sent", "paid")
        invoice_transition("sent", "overdue")
        invoice_transition("sent", "uncollectible")
        invoice_transition("overdue", "paid")
        invoice_transition("paid", "sent")  # 退款回退
        for bad in (("paid", "voided"), ("voided", "sent"), ("draft", "paid"),
                    ("uncollectible", "paid"), ("paid", "overdue")):
            with self.assertRaises(BillingStateError, msg=f"{bad}"):
                invoice_transition(*bad)

    def test_refund_and_reservation_reject_illegal(self):
        refund_transition("pending", "completed")
        refund_transition("pending", "rejected")
        with self.assertRaises(BillingStateError):
            refund_transition("completed", "pending")
        reservation_transition("reserved", "committed")
        reservation_transition("reserved", "released")
        reservation_transition("reserved", "expired")
        with self.assertRaises(BillingStateError):
            reservation_transition("committed", "reserved")
        with self.assertRaises(BillingStateError):
            reservation_transition("released", "committed")


if __name__ == "__main__":
    unittest.main()
