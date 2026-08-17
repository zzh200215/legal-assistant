"""计费（发票/订阅/支付事件/对账） 任务：从 app.tasks.__init__ 拆出（P3 上帝文件拆分）。"""
from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.obs_context import enqueue_headers as obs_enqueue_headers
from app.core.time import utc_now
from app.tasks.runtime import (
    beat_lock as _beat_lock,
    record_beat_heartbeat as _record_beat_heartbeat,
)

import uuid


@celery_app.task(name="scan_overdue_invoices")
@_beat_lock(task_name="scan_overdue_invoices", ttl_seconds=7200)
def scan_overdue_invoices_task():
    """每小时：将已超过 due_date 且仍为 sent 状态的账单标记为 overdue。"""
    _record_beat_heartbeat()
    from app.models.legal_billing import LegalInvoice
    from datetime import date

    db = SessionLocal()
    today = date.today()
    updated = 0
    try:
        overdue = db.query(LegalInvoice).filter(
            LegalInvoice.status == "sent",
            LegalInvoice.due_date.isnot(None),
            LegalInvoice.due_date < today,
        ).all()
        for inv in overdue:
            inv.status = "overdue"
            updated += 1
        db.commit()
        return {"marked_overdue": updated}
    finally:
        db.close()


@celery_app.task(name="scan_expired_subscriptions")
@_beat_lock(task_name="scan_expired_subscriptions", ttl_seconds=7200)
def scan_expired_subscriptions_task():
    """每小时：将已过 current_period_end 的 active 订阅置为 expired（配额回落免费版）。"""
    _record_beat_heartbeat()
    from app.services.billing.subscription_service import subscription_service

    db = SessionLocal()
    try:
        return {"expired_subscriptions": subscription_service.expire_overdue_subscriptions(db)}
    finally:
        db.close()


@celery_app.task(name="dispatch_payment_events")
@_beat_lock(task_name="dispatch_payment_events", ttl_seconds=180)
def dispatch_payment_events_task():
    """每60秒：领取 pending 支付事件并异步处理（幂等、可重放、乱序保护）。

    Webhook 仅负责验签与持久化；权益/账单变更在 worker 内执行，
    失败可重试，worker 崩溃由 recover 任务回收。
    """
    _record_beat_heartbeat()
    from app.services.billing.payment_event_service import payment_event_service

    db = SessionLocal()
    owner = f"payment-event:{uuid.uuid4().hex}"
    try:
        processed = 0
        while processed < 200:
            batch = payment_event_service.claim_pending(db=db, owner=owner)
            if not batch:
                break
            for event in batch:
                payment_event_service.process_event(db, event)
                processed += 1
        return {"processed": processed}
    finally:
        db.close()


@celery_app.task(name="recover_stale_payment_events")
@_beat_lock(task_name="recover_stale_payment_events", ttl_seconds=600)
def recover_stale_payment_events_task():
    """每5分钟：回收租约过期的 payment event（worker 崩溃后安全重领）。"""
    _record_beat_heartbeat()
    from app.services.billing.payment_event_service import payment_event_service

    db = SessionLocal()
    try:
        return {"reclaimed": payment_event_service.reclaim_stale(db=db)}
    finally:
        db.close()


# ── 每日对账 ─────────────────────────────────────────────────────────

@celery_app.task(name="run_daily_reconciliation")
@_beat_lock(task_name="run_daily_reconciliation", ttl_seconds=1800)
def run_daily_reconciliation_task():
    """每日对账（本地一致性）：未处理 webhook、卡住收款、发票/退款差异。

    不自动修改财务记录；差异入 reconciliation_discrepancies 供人工处理。
    """
    _record_beat_heartbeat()
    from app.services.billing.reconciliation_service import reconciliation_service

    db = SessionLocal()
    try:
        run_date = utc_now().strftime("%Y-%m-%d")
        return reconciliation_service.run(db=db, run_date=run_date, provider="local", owner="daily")
    finally:
        db.close()


@celery_app.task(name="recover_stale_reconciliation_runs")
@_beat_lock(task_name="recover_stale_reconciliation_runs", ttl_seconds=1800)
def recover_stale_reconciliation_runs_task():
    """回收租约过期的对账 run（中断后可重跑，不重复修正财务记录）。"""
    _record_beat_heartbeat()
    from app.services.billing.reconciliation_service import reconciliation_service

    db = SessionLocal()
    try:
        runs = reconciliation_service.recover_stale_runs(db=db)
        return {"recovered": len(runs)}
    finally:
        db.close()
