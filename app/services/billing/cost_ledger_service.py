"""统一成本台账（app/services/cost_ledger_service.py）。

- 追加式、不可变：禁止覆盖/删除既有财务记录；修正使用 adjustment / reversal。
- 金额统一 Decimal + Numeric(18,6)，禁止 float 参与计算与存储。
- UNIQUE(scope, idempotency_key)：同一来源事件重复处理只生成一条。
- 币种不同不得直接相加；无汇率场景下跨币种分别记账。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models.cost_ledger import CostLedgerEntry


def _decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _entry_id() -> str:
    return f"{utc_now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:12]}"


class CostLedgerService:

    def record(
        self,
        *,
        db: Session,
        tenant_id: int | None,
        entry_type: str,
        direction: str,
        amount: Decimal | float | str | int,
        currency: str = "CNY",
        quantity: Decimal | float | str | int | None = None,
        unit: str | None = None,
        unit_price: Decimal | float | str | int | None = None,
        source_type: str | None = None,
        source_id: str | None = None,
        billing_period: str | None = None,
        scope: str = "billing",
        idempotency_key: str | None = None,
        metadata_summary: str | None = None,
        user_id: int | None = None,
        occurred_at: datetime | None = None,
    ) -> CostLedgerEntry:
        """登记一条台账（幂等：scope+key 已存在则返回既有，不重复入账）。"""
        if direction not in ("cost", "charge", "payment", "refund", "adjustment"):
            raise ValueError(f"未知台账方向: {direction}")
        amount_dec = _decimal(amount)
        key = idempotency_key or f"{source_type}:{source_id}:{entry_type}:{direction}"
        existing = db.query(CostLedgerEntry).filter(
            CostLedgerEntry.scope == scope,
            CostLedgerEntry.idempotency_key == key,
        ).first()
        if existing is not None:
            return existing

        entry = CostLedgerEntry(
            entry_id=_entry_id(),
            tenant_id=tenant_id,
            user_id=user_id,
            entry_type=entry_type,
            direction=direction,
            amount=amount_dec,
            currency=currency,
            quantity=_decimal(quantity) if quantity is not None else None,
            unit=unit,
            unit_price=_decimal(unit_price) if unit_price is not None else None,
            source_type=source_type,
            source_id=str(source_id) if source_id is not None else None,
            billing_period=billing_period,
            scope=scope,
            idempotency_key=key,
            metadata_summary=(metadata_summary or "")[:1000],
            occurred_at=occurred_at or utc_now(),
        )
        db.add(entry)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            existing = db.query(CostLedgerEntry).filter(
                CostLedgerEntry.scope == scope,
                CostLedgerEntry.idempotency_key == key,
            ).first()
            if existing is None:
                raise
            return existing
        return entry

    # ── 便捷入口 ─────────────────────────────────────────────────

    def record_llm_cost(self, *, db: Session, user_id: int, model: str, action: str,
                        cost: Decimal | float | str, prompt_tokens: int,
                        completion_tokens: int, token_usage_id: int,
                        occurred_at: datetime | None = None) -> CostLedgerEntry:
        """模型调用成本入账（来源 = token_usage 行，幂等去重）。"""
        from app.models.user import User

        user = db.query(User).filter(User.id == user_id).first()
        tenant_id = user.organization_id if user else None
        occurred_at = occurred_at or utc_now()
        period = occurred_at.strftime("%Y-%m")
        return self.record(
            db=db,
            tenant_id=tenant_id,
            user_id=user_id,
            entry_type="llm_call",
            direction="cost",
            amount=cost,
            currency="CNY",
            quantity=Decimal(str(prompt_tokens + completion_tokens)),
            unit="tokens",
            source_type="llm_run",
            source_id=str(token_usage_id),
            billing_period=period,
            scope="llm_cost",
            idempotency_key=f"llm:{token_usage_id}",
            metadata_summary=json.dumps({"model": model, "action": action}, ensure_ascii=False),
            occurred_at=occurred_at,
        )

    def record_payment(self, *, db: Session, invoice_id: int, organization_id: int,
                       amount: Decimal, payment_record_id: int,
                       recorded_by: int, currency: str = "CNY",
                       occurred_at: datetime | None = None) -> CostLedgerEntry:
        """律所收款入账（charge）。"""
        occurred_at = occurred_at or utc_now()
        return self.record(
            db=db, tenant_id=organization_id, user_id=recorded_by,
            entry_type="payment", direction="payment", amount=amount, currency=currency,
            source_type="payment_record", source_id=str(payment_record_id),
            billing_period=occurred_at.strftime("%Y-%m"),
            scope="billing", idempotency_key=f"payment:{payment_record_id}",
            metadata_summary=f"invoice_id={invoice_id}", occurred_at=occurred_at,
        )

    def record_refund(self, *, db: Session, invoice_id: int, organization_id: int,
                      amount: Decimal, refund_record_id: int,
                      recorded_by: int, currency: str = "CNY",
                      occurred_at: datetime | None = None) -> CostLedgerEntry:
        """退款入账（refund）。"""
        occurred_at = occurred_at or utc_now()
        return self.record(
            db=db, tenant_id=organization_id, user_id=recorded_by,
            entry_type="refund", direction="refund", amount=amount, currency=currency,
            source_type="refund_record", source_id=str(refund_record_id),
            billing_period=occurred_at.strftime("%Y-%m"),
            scope="billing", idempotency_key=f"refund:{refund_record_id}",
            metadata_summary=f"invoice_id={invoice_id}", occurred_at=occurred_at,
        )

    def record_platform_payment(self, *, db: Session, platform_payment,
                                direction: str = "payment",
                                amount: Decimal | None = None) -> CostLedgerEntry:
        """平台订阅收款/退款入账。"""
        occurred_at = platform_payment.confirmed_at or platform_payment.created_at or utc_now()
        amount = amount if amount is not None else _decimal(platform_payment.amount)
        entry_type = "refund" if direction == "refund" else "plan_subscription"
        return self.record(
            db=db, tenant_id=platform_payment.organization_id,
            user_id=platform_payment.user_id,
            entry_type=entry_type, direction=direction, amount=amount,
            currency=platform_payment.currency or "CNY",
            source_type="platform_payment", source_id=str(platform_payment.id),
            billing_period=occurred_at.strftime("%Y-%m"),
            scope="platform_billing",
            idempotency_key=f"platform_payment:{platform_payment.id}:{direction}",
            metadata_summary=f"plan={platform_payment.plan_tier}",
            occurred_at=occurred_at,
        )

    # ── 汇总查询（对账依据）────────────────────────────────────────

    def sum_by(self, db: Session, *, tenant_id: int | None, direction: str,
               billing_period: str | None = None) -> Decimal:
        from sqlalchemy import func as sa_func

        query = db.query(sa_func.coalesce(sa_func.sum(CostLedgerEntry.amount), 0)).filter(
            CostLedgerEntry.direction == direction)
        if tenant_id is not None:
            query = query.filter(CostLedgerEntry.tenant_id == tenant_id)
        if billing_period:
            query = query.filter(CostLedgerEntry.billing_period == billing_period)
        return Decimal(str(query.scalar() or 0))


cost_ledger_service = CostLedgerService()
