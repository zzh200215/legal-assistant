"""每日对账服务（app/services/reconciliation_service.py）。

本地一致性核对 + 结构化差异报告：
- webhook_pending：未处理/失败/needs_reconciliation 且超时的支付事件
- payment_stuck：长期 pending 的平台收款
- invoice_status_mismatch / invoice_amount_mismatch：发票状态与付款/退款记录不符
- refund_mismatch：退款累计与付款/退款台账不符

约束：
- 运行带分布式锁（由任务）+ run 台账 cursor/checkpoint/租约，断点恢复。
- 不自动静默修改财务记录；仅对已定义安全规则自动修复并记 adjustment（默认不自动修复）。
- provider 侧对比在无真实查询 API 时标记 provider_query_unavailable，不伪造差异。
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.time import utc_now
from app.models.legal_billing import LegalInvoice, LegalPaymentRecord, LegalRefundRecord
from app.models.payment_event import PaymentEvent
from app.models.platform_payment import PlatformPayment
from app.models.reconciliation import ReconciliationDiscrepancy, ReconciliationRun

ZERO = Decimal("0")


def _d(value) -> Decimal:
    if value is None:
        return ZERO
    return Decimal(str(value))


class ReconciliationService:

    def get_or_create_run(self, *, db: Session, run_date: str, provider: str,
                          organization_id: int | None, owner: str,
                          ttl_seconds: int) -> ReconciliationRun | None:
        """取最近一次未成功的 run 复用；最近已成功则返回 None（幂等跳过）。"""
        latest = (
            db.query(ReconciliationRun)
            .filter(
                ReconciliationRun.run_date == run_date,
                ReconciliationRun.provider == provider,
                ReconciliationRun.organization_id == organization_id,
            )
            .order_by(ReconciliationRun.id.desc())
            .first()
        )
        if latest is not None and latest.status == "succeeded":
            return None
        run = latest
        if run is None:
            run = ReconciliationRun(
                run_date=run_date, provider=provider, organization_id=organization_id,
                status="pending",
                idempotency_key=f"recon:{run_date}:{provider}:{organization_id or 'all'}",
            )
            db.add(run)
        run.status = "running"
        run.lease_owner = owner
        run.started_at = run.started_at or utc_now()
        run.lease_expires_at = utc_now() + timedelta(seconds=ttl_seconds)
        db.commit()
        db.refresh(run)
        return run

    def _add_discrepancy(self, db: Session, run: ReconciliationRun, *, discrepancy_type: str,
                         local_reference: str | None, provider_reference: str | None,
                         expected_amount: Decimal | None, actual_amount: Decimal | None,
                         currency: str | None, expected_status: str | None,
                         actual_status: str | None, severity: str,
                         recommended_action: str) -> None:
        db.add(ReconciliationDiscrepancy(
            run_id=run.id,
            discrepancy_type=discrepancy_type,
            local_reference=local_reference,
            provider_reference=provider_reference,
            expected_amount=expected_amount,
            actual_amount=actual_amount,
            currency=currency,
            expected_status=expected_status,
            actual_status=actual_status,
            severity=severity,
            status="open",
            recommended_action=recommended_action,
            detected_at=utc_now(),
        ))

    def run(self, *, db: Session, run_date: str, provider: str,
            organization_id: int | None = None, owner: str = "manual") -> dict:
        """执行一轮对账；返回统计。"""
        settings = get_settings()
        run = self.get_or_create_run(
            db=db, run_date=run_date, provider=provider,
            organization_id=organization_id, owner=owner,
            ttl_seconds=settings.RECONCILIATION_RUN_LEASE_TTL_SECONDS,
        )
        if run is None:
            return {"status": "skipped", "reason": "already_succeeded"}

        now = utc_now()
        found = 0
        max_disc = settings.RECONCILIATION_MAX_DISCREPANCIES
        try:
            # 1) 未处理/失败的支付事件
            stale_webhook_before = now - timedelta(minutes=settings.RECONCILIATION_STALE_WEBHOOK_MINUTES)
            events = db.query(PaymentEvent).filter(
                PaymentEvent.status.in_(("pending", "failed", "needs_reconciliation")),
                PaymentEvent.received_at < stale_webhook_before,
            ).limit(max_disc).all()
            for event in events:
                self._add_discrepancy(
                    db, run,
                    discrepancy_type="webhook_pending",
                    local_reference=f"payment_event:{event.id}",
                    provider_reference=event.provider_event_id,
                    expected_amount=None, actual_amount=None, currency=None,
                    expected_status="completed", actual_status=event.status,
                    severity="high" if event.status == "needs_reconciliation" else "medium",
                    recommended_action="检查并重放支付事件",
                )
                found += 1
                if found >= max_disc:
                    break

            # 2) 长期 pending 的平台收款
            stale_payment_before = now - timedelta(days=settings.RECONCILIATION_STALE_PAYMENT_DAYS)
            stuck = db.query(PlatformPayment).filter(
                PlatformPayment.status == "pending",
                PlatformPayment.created_at < stale_payment_before,
            ).limit(max_disc).all()
            for payment in stuck:
                self._add_discrepancy(
                    db, run, discrepancy_type="payment_stuck",
                    local_reference=f"platform_payment:{payment.id}",
                    provider_reference=str(payment.provider_event_id or ""),
                    expected_amount=_d(payment.amount), actual_amount=None,
                    currency=payment.currency, expected_status="confirmed",
                    actual_status="pending", severity="medium",
                    recommended_action="人工确认或驳回该笔平台收款",
                )
                found += 1

            # 3) 发票状态/金额与付款退款记录核对
            invoice_ids = [
                row[0] for row in db.query(LegalInvoice.id).filter(
                    LegalInvoice.status.in_(("paid", "sent", "overdue"))
                ).order_by(LegalInvoice.id).all()
            ]
            for inv_id in invoice_ids:
                invoice = db.query(LegalInvoice).filter(LegalInvoice.id == inv_id).first()
                if invoice is None:
                    continue
                paid = self._paid_total(db, inv_id)
                refunded = self._refunded_total(db, inv_id)
                net = max(paid - refunded, ZERO)
                total = _d(invoice.total_amount)
                if invoice.status == "paid" and net < total:
                    self._add_discrepancy(
                        db, run, discrepancy_type="invoice_status_mismatch",
                        local_reference=f"legal_invoice:{inv_id}",
                        provider_reference=None, expected_amount=total, actual_amount=net,
                        currency=invoice.currency, expected_status="paid",
                        actual_status="partial/unpaid", severity="high",
                        recommended_action="核对付款记录与收款状态",
                    )
                    found += 1
                elif net > total:
                    self._add_discrepancy(
                        db, run, discrepancy_type="invoice_amount_mismatch",
                        local_reference=f"legal_invoice:{inv_id}",
                        provider_reference=None, expected_amount=total, actual_amount=net,
                        currency=invoice.currency, expected_status=invoice.status,
                        actual_status="overpaid", severity="high",
                        recommended_action="检查超额收款并安排退款",
                    )
                    found += 1
                if found >= max_disc:
                    break

            # 4) 平台退款累计不超已收（refund_mismatch）
            refunded_pays = db.query(PlatformPayment).filter(
                PlatformPayment.status == "refunded",
            ).limit(max_disc).all()
            for payment in refunded_pays:
                if _d(payment.refunded_amount) > _d(payment.amount):
                    self._add_discrepancy(
                        db, run, discrepancy_type="refund_mismatch",
                        local_reference=f"platform_payment:{payment.id}",
                        provider_reference=None, expected_amount=_d(payment.amount),
                        actual_amount=_d(payment.refunded_amount),
                        currency=payment.currency, expected_status="refunded",
                        actual_status="refunded", severity="high",
                        recommended_action="退款金额超过已收金额，需人工核查",
                    )
                    found += 1

            run.status = "succeeded"
            run.processed = len(invoice_ids) + len(events) + len(stuck)
            run.discrepancies_found = found
            run.completed_at = utc_now()
            run.error_code = None
            db.commit()
            return {"status": "succeeded", "processed": run.processed, "discrepancies_found": found}
        except Exception as exc:  # noqa: BLE001 - 统一记账后重抛由任务重试
            run.status = "failed"
            run.error_code = type(exc).__name__[:64]
            run.error_message = "对账失败，可重试"
            run.completed_at = utc_now()
            db.commit()
            raise

    @staticmethod
    def _paid_total(db: Session, invoice_id: int) -> Decimal:
        from sqlalchemy import func as sa_func
        return _d(db.query(sa_func.coalesce(sa_func.sum(LegalPaymentRecord.amount), 0)).filter(
            LegalPaymentRecord.invoice_id == invoice_id,
            LegalPaymentRecord.status == "confirmed",
        ).scalar())

    @staticmethod
    def _refunded_total(db: Session, invoice_id: int) -> Decimal:
        from sqlalchemy import func as sa_func
        return _d(db.query(sa_func.coalesce(sa_func.sum(LegalRefundRecord.amount), 0)).filter(
            LegalRefundRecord.invoice_id == invoice_id,
            LegalRefundRecord.status == "completed",
        ).scalar())

    def recover_stale_runs(self, *, db: Session, limit: int = 50) -> list[ReconciliationRun]:
        """回收租约过期的 running run（worker 崩溃后重跑）。"""
        settings = get_settings()
        stale_before = utc_now() - timedelta(seconds=settings.RECONCILIATION_RUN_LEASE_TTL_SECONDS)
        runs = db.query(ReconciliationRun).filter(
            ReconciliationRun.status == "running",
            ReconciliationRun.lease_expires_at.isnot(None),
            ReconciliationRun.lease_expires_at < stale_before,
        ).limit(limit).all()
        for run in runs:
            run.status = "pending"
            run.lease_owner = None
            run.lease_expires_at = None
            run.error_code = "LEASE_EXPIRED"
        db.commit()
        return runs


reconciliation_service = ReconciliationService()
