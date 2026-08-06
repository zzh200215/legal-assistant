"""Phase 11 — 法律业务定时任务模块

Celery periodic tasks for legal domain:
- 每15分钟：扫描关键日期到期提醒
- 每小时：检查逾期账单
- 每小时：检查过期门户链接
- 每5分钟：投递待发送通知
- 每5分钟：重试失败通知

注意：celery_app.conf.beat_schedule 中的任务注册在 app.core.celery_app 中，
本模块提供任务函数实现。现有 tasks/__init__.py 中已有部分同名任务的简单实现，
本模块提供完整的服务层调用版本，可通过替换 tasks 中的 import 来切换。
"""
from __future__ import annotations

import logging
from datetime import date

from app.core.celery_app import celery_app
from app.core.database import SessionLocal
from app.core.time import utc_now
from app.services.oplog_service import oplog_service

logger = logging.getLogger(__name__)


def _heartbeat() -> None:
    """记录 beat 心跳。"""
    try:
        import redis
        from app.core.config import get_settings
        redis.from_url(get_settings().REDIS_URL).set(
            "aibg:operations:legal_beat:last_tick",
            utc_now().isoformat(),
            ex=180,
        )
    except Exception:
        pass


# ── 关键日期提醒扫描 ──────────────────────────────────────────────

@celery_app.task(name="legal_scan_deadline_reminders")
def legal_scan_deadline_reminders_task() -> dict:
    """每15分钟：扫描活跃关键日期，为到期偏移创建通知事件并投递。

    幂等保证：同一 (deadline_id, channel, offset) 只发送一次。
    失败重试：最多3次。
    """
    _heartbeat()
    db = SessionLocal()
    try:
        from app.services.deadline_service import deadline_service

        # 1. 扫描需要提醒的关键日期
        created = deadline_service.scan_due_reminders(db=db)
        logger.info("deadline_reminder: created %d events", len(created))

        # 2. 标记已到期但未完成的关键日期
        due_count = deadline_service.mark_due_deadlines(db=db)
        logger.info("deadline_reminder: marked %d deadlines as due", due_count)

        # 3. 投递待发送提醒
        dispatch_result = deadline_service.dispatch_pending_reminders(db=db)
        logger.info("deadline_reminder: dispatched sent=%d, failed=%d",
                     dispatch_result["sent"], dispatch_result["failed"])

        # 4. 重试失败通知
        retried = deadline_service.retry_failed_reminders(db=db)
        logger.info("deadline_reminder: retried %d failed events", retried)

        return {
            "created_events": len(created),
            "marked_due": due_count,
            "dispatched": dispatch_result,
            "retried": retried,
        }
    except Exception as exc:
        logger.error("legal_scan_deadline_reminders 任务失败: %s", exc, exc_info=True)
        return {"error": str(exc)}
    finally:
        db.close()


# ── 逾期账单扫描 ──────────────────────────────────────────────────

@celery_app.task(name="legal_scan_overdue_invoices")
def legal_scan_overdue_invoices_task() -> dict:
    """每小时：将已过 due_date 且仍为 sent 状态的账单标记为 overdue。

    同时检查部分付款超期的情况。
    """
    _heartbeat()
    db = SessionLocal()
    try:
        from app.models.legal_billing import LegalInvoice

        today = date.today()
        now = utc_now()

        # 标记逾期账单
        overdue_invoices = db.query(LegalInvoice).filter(
            LegalInvoice.status == "sent",
            LegalInvoice.due_date.isnot(None),
            LegalInvoice.due_date < today,
        ).all()

        marked = 0
        for inv in overdue_invoices:
            inv.status = "overdue"
            marked += 1

        # 也检查部分付款但已逾期的
        partial_overdue = db.query(LegalInvoice).filter(
            LegalInvoice.payment_progress == "partial_paid",
            LegalInvoice.status.in_(["sent", "paid"]),
            LegalInvoice.due_date.isnot(None),
            LegalInvoice.due_date < today,
        ).all()

        partial_marked = 0
        for inv in partial_overdue:
            if inv.status != "overdue":
                inv.status = "overdue"
                partial_marked += 1

        db.commit()

        if marked > 0 or partial_marked > 0:
            logger.info("scan_overdue_invoices: marked %d overdue, %d partial overdue",
                        marked, partial_marked)

        return {
            "marked_overdue": marked,
            "marked_partial_overdue": partial_marked,
        }
    except Exception as exc:
        logger.error("legal_scan_overdue_invoices 任务失败: %s", exc, exc_info=True)
        return {"error": str(exc)}
    finally:
        db.close()


# ── 过期门户链接扫描 ──────────────────────────────────────────────

@celery_app.task(name="legal_scan_expired_portal_links")
def legal_scan_expired_portal_links_task() -> dict:
    """每小时：将已过 expires_at 的门户链接状态改为 expired。

    同时检查访问次数达到上限的链接。
    """
    _heartbeat()
    db = SessionLocal()
    try:
        from app.models.legal_portal import LegalPortalLink

        now = utc_now()

        # 标记过期链接
        expired_links = db.query(LegalPortalLink).filter(
            LegalPortalLink.status == "active",
            LegalPortalLink.is_permanent == 0,
            LegalPortalLink.expires_at.isnot(None),
            LegalPortalLink.expires_at < now,
        ).all()

        expired_count = 0
        for link in expired_links:
            link.status = "expired"
            expired_count += 1

        # 检查访问次数达到上限
        access_limited = db.query(LegalPortalLink).filter(
            LegalPortalLink.status == "active",
            LegalPortalLink.max_access_count.isnot(None),
            LegalPortalLink.access_count >= LegalPortalLink.max_access_count,
        ).all()

        limited_count = 0
        for link in access_limited:
            link.status = "access_limited"
            limited_count += 1

        db.commit()

        if expired_count > 0 or limited_count > 0:
            logger.info("scan_expired_portal_links: expired=%d, access_limited=%d",
                        expired_count, limited_count)

        return {
            "expired_links": expired_count,
            "access_limited_links": limited_count,
        }
    except Exception as exc:
        logger.error("legal_scan_expired_portal_links 任务失败: %s", exc, exc_info=True)
        return {"error": str(exc)}
    finally:
        db.close()


# ── 通知投递 ──────────────────────────────────────────────────────

@celery_app.task(name="legal_dispatch_notifications")
def legal_dispatch_notifications_task() -> dict:
    """每5分钟：投递所有待发送的通知事件。"""
    _heartbeat()
    db = SessionLocal()
    try:
        from app.services.notification_service import notification_service

        result = notification_service.dispatch_pending(db=db)
        logger.info("dispatch_notifications: %s", result)
        return result
    except Exception as exc:
        logger.error("legal_dispatch_notifications 任务失败: %s", exc, exc_info=True)
        return {"error": str(exc)}
    finally:
        db.close()


# ── 失败通知重试 ──────────────────────────────────────────────────

@celery_app.task(name="legal_retry_failed_notifications")
def legal_retry_failed_notifications_task() -> dict:
    """每5分钟：重试失败的通知事件，最多3次。"""
    _heartbeat()
    db = SessionLocal()
    try:
        from app.services.notification_service import notification_service

        retried = notification_service.retry_failed(db=db, max_retries=3)
        logger.info("retry_failed_notifications: retried %d", retried)
        return {"retried": retried}
    except Exception as exc:
        logger.error("legal_retry_failed_notifications 任务失败: %s", exc, exc_info=True)
        return {"error": str(exc)}
    finally:
        db.close()


# ── 催收提醒扫描 ──────────────────────────────────────────────────

@celery_app.task(name="legal_scan_collection_reminders")
def legal_scan_collection_reminders_task() -> dict:
    """每天：扫描逾期账单，自动创建并发送催收提醒。

    逾期超过7天且催收次数 < 3 的账单自动催收。
    """
    _heartbeat()
    db = SessionLocal()
    try:
        from app.models.legal_billing import LegalInvoice, LegalCollectionReminder
        from datetime import timedelta

        now = utc_now()
        overdue_threshold = date.today() - timedelta(days=7)

        # 查找需要催收的账单
        overdue_invoices = db.query(LegalInvoice).filter(
            LegalInvoice.status == "overdue",
            LegalInvoice.due_date.isnot(None),
            LegalInvoice.due_date <= overdue_threshold,
            LegalInvoice.collection_count < 3,
        ).all()

        sent = 0
        for inv in overdue_invoices:
            try:
                from app.services.billing_service import billing_service

                # 创建催收提醒
                reminder = billing_service.create_collection_reminder(
                    db=db,
                    invoice_id=inv.id,
                    organization_id=inv.organization_id,
                    created_by=inv.created_by,
                    note=f"自动催收（逾期 {(date.today() - inv.due_date).days} 天）",
                )

                # 发送催收提醒
                billing_service.send_collection_reminder(
                    db=db,
                    reminder_id=reminder.id,
                )
                sent += 1
            except Exception as exc:
                logger.warning("催收提醒发送失败 invoice_id=%s: %s", inv.id, exc)

        db.commit()

        return {"sent_reminders": sent, "checked_invoices": len(overdue_invoices)}
    except Exception as exc:
        logger.error("legal_scan_collection_reminders 任务失败: %s", exc, exc_info=True)
        return {"error": str(exc)}
    finally:
        db.close()
