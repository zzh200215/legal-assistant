"""统一订阅/计费状态机（app/services/billing_state_machines.py）。

所有订阅、支付、发票、退款、配额预留的状态迁移必须经此集中校验，
禁止调用方任意更新状态。每次迁移由调用方记录操作者/时间/原因/来源事件。

- Subscription: pending/trialing -> active -> past_due -> suspended -> cancelled/expired
- PlatformPayment: pending -> confirmed / rejected；confirmed -> refunded
- LegalPaymentRecord: confirmed -> refunded / disputed
- LegalInvoice: draft -> sent -> paid/overdue -> voided / uncollectible；
  paid -> sent 仅用于退款使收款进度回退到未满额（兼容既有 _update_payment_progress）
- UsageReservation: reserved -> committed / released / expired
"""

from __future__ import annotations

# ── Subscription ──────────────────────────────────────────────────────
SUB_ACTIVE = "active"
SUB_PAST_DUE = "past_due"
SUB_SUSPENDED = "suspended"
SUB_CANCELLED = "cancelled"
SUB_EXPIRED = "expired"
SUB_PENDING = "pending"
SUB_TRIALING = "trialing"

SUBSCRIPTION_TRANSITIONS: dict[str, frozenset] = {
    SUB_PENDING: frozenset({SUB_ACTIVE, SUB_CANCELLED, SUB_EXPIRED}),
    SUB_TRIALING: frozenset({SUB_ACTIVE, SUB_CANCELLED, SUB_EXPIRED}),
    SUB_ACTIVE: frozenset({SUB_PAST_DUE, SUB_SUSPENDED, SUB_CANCELLED, SUB_EXPIRED}),
    SUB_PAST_DUE: frozenset({SUB_ACTIVE, SUB_SUSPENDED, SUB_CANCELLED, SUB_EXPIRED}),
    SUB_SUSPENDED: frozenset({SUB_ACTIVE, SUB_CANCELLED, SUB_EXPIRED}),
    SUB_CANCELLED: frozenset(),
    SUB_EXPIRED: frozenset(),
}

# ── PlatformPayment（对公转账 / 平台收款）──────────────────────────────
PAY_PENDING = "pending"
PAY_CONFIRMED = "confirmed"
PAY_REJECTED = "rejected"
PAY_REFUNDED = "refunded"

PLATFORM_PAYMENT_TRANSITIONS: dict[str, frozenset] = {
    PAY_PENDING: frozenset({PAY_CONFIRMED, PAY_REJECTED}),
    PAY_CONFIRMED: frozenset({PAY_REFUNDED}),
    PAY_REJECTED: frozenset(),
    PAY_REFUNDED: frozenset(),
}

# ── LegalPaymentRecord（律所收款）──────────────────────────────────────
PR_CONFIRMED = "confirmed"
PR_REFUNDED = "refunded"
PR_DISPUTED = "disputed"

PAYMENT_RECORD_TRANSITIONS: dict[str, frozenset] = {
    PR_CONFIRMED: frozenset({PR_REFUNDED, PR_DISPUTED}),
    PR_REFUNDED: frozenset(),
    PR_DISPUTED: frozenset(),
}

# ── LegalInvoice ──────────────────────────────────────────────────────
INV_DRAFT = "draft"
INV_SENT = "sent"
INV_PAID = "paid"
INV_OVERDUE = "overdue"
INV_VOIDED = "voided"
INV_UNCOLLECTIBLE = "uncollectible"

INVOICE_TRANSITIONS: dict[str, frozenset] = {
    INV_DRAFT: frozenset({INV_SENT, INV_VOIDED, INV_UNCOLLECTIBLE}),
    INV_SENT: frozenset({INV_PAID, INV_OVERDUE, INV_VOIDED, INV_UNCOLLECTIBLE}),
    INV_OVERDUE: frozenset({INV_PAID, INV_UNCOLLECTIBLE, INV_VOIDED}),
    INV_PAID: frozenset({INV_SENT}),
    INV_VOIDED: frozenset(),
    INV_UNCOLLECTIBLE: frozenset(),
}

# ── LegalRefundRecord ────────────────────────────────────────────────────────
REFUND_TRANSITIONS: dict[str, frozenset] = {
    "pending": frozenset({"completed", "rejected"}),
    "approved": frozenset({"completed", "rejected"}),  # 兼容历史状态
    "completed": frozenset(),
    "rejected": frozenset(),
}

# ── UsageReservation ──────────────────────────────────────────────────
RES_RESERVED = "reserved"
RES_COMMITTED = "committed"
RES_RELEASED = "released"
RES_EXPIRED = "expired"

RESERVATION_TRANSITIONS: dict[str, frozenset] = {
    RES_RESERVED: frozenset({RES_COMMITTED, RES_RELEASED, RES_EXPIRED}),
    RES_COMMITTED: frozenset(),
    RES_RELEASED: frozenset(),
    RES_EXPIRED: frozenset(),
}


class BillingStateError(ValueError):
    """状态机非法跳转。"""


_TRANSITION_TABLES: dict[str, dict[str, frozenset]] = {
    "subscription": SUBSCRIPTION_TRANSITIONS,
    "platform_payment": PLATFORM_PAYMENT_TRANSITIONS,
    "payment_record": PAYMENT_RECORD_TRANSITIONS,
    "invoice": INVOICE_TRANSITIONS,
    "refund": REFUND_TRANSITIONS,
    "usage_reservation": RESERVATION_TRANSITIONS,
}


def transition(kind: str, current: str | None, to: str) -> None:
    """校验并返回是否允许 current -> to；非法跳转抛 BillingStateError。"""
    table = _TRANSITION_TABLES.get(kind)
    if table is None:
        raise BillingStateError(f"未知状态机类型: {kind}")
    allowed = table.get(current or "", frozenset())
    if to not in allowed:
        raise BillingStateError(f"状态机拒绝迁移: {kind}.{current} -> {to}")
    return None


def subscription_transition(current: str, to: str) -> None:
    transition("subscription", current, to)


def platform_payment_transition(current: str, to: str) -> None:
    transition("platform_payment", current, to)


def payment_record_transition(current: str, to: str) -> None:
    transition("payment_record", current, to)


def invoice_transition(current: str, to: str) -> None:
    transition("invoice", current, to)


def refund_transition(current: str, to: str) -> None:
    transition("refund", current, to)


def reservation_transition(current: str, to: str) -> None:
    transition("usage_reservation", current, to)
