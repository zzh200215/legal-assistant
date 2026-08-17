"""通知/邮件 Outbox/Webhook 投递 任务：从 app.tasks.__init__ 拆出（P3 上帝文件拆分）。"""
from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.obs_context import enqueue_headers as obs_enqueue_headers
from app.core.time import utc_now
from app.tasks.runtime import (
    beat_lock as _beat_lock,
    record_beat_heartbeat as _record_beat_heartbeat,
)

import json
import uuid


@celery_app.task(name="dispatch_notification_events")
@_beat_lock(task_name="dispatch_notification_events", ttl_seconds=180)
def dispatch_notification_events_task():
    """每60秒：Outbox 领取并投递已到提醒时间的 pending 通知事件。

    站内通知标记为 delivered（进入铃铛未读）；邮件渠道真实投递（创建
    EmailSendRequest 邮件 Outbox，内部低风险自动批准，需审批的等待人工审批）。
    领取采用 keyset 原子 claim + 租约，worker 崩溃后由 recover 任务回收。
    """
    _record_beat_heartbeat()
    from app.services.notification.notification_service import notification_service

    db = SessionLocal()
    try:
        return notification_service.dispatch_pending(db=db)
    finally:
        db.close()


@celery_app.task(name="deliver_email_send_requests")
@_beat_lock(task_name="deliver_email_send_requests", ttl_seconds=300)
def deliver_email_send_requests_task():
    """每60秒：领取已批准/可重试的 EmailSendRequest（邮件 Outbox）并投递。

    幂等：claim 原子领取 + 租约；同请求不会重复发送；写超时按 AMBIGUOUS 不盲目重试。
    不可恢复/重试耗尽进入 dead letter，人工重试保留原幂等键。
    """
    _record_beat_heartbeat()
    from app.services.notification.outbound_email_service import outbound_email_service

    db = SessionLocal()
    owner = f"email-deliver:{uuid.uuid4().hex}"
    try:
        delivered = 0
        while delivered < 200:
            batch = outbound_email_service.claim_pending_batch(db=db, owner=owner)
            if not batch:
                break
            for request in batch:
                try:
                    outbound_email_service._perform_send(db=db, request=request, owner=owner)
                    db.commit()
                    delivered += 1
                except Exception:
                    db.rollback()
        return {"delivered": delivered}
    finally:
        db.close()


@celery_app.task(name="recover_stale_outbox_claims")
@_beat_lock(task_name="recover_stale_outbox_claims", ttl_seconds=600)
def recover_stale_outbox_claims_task():
    """每5分钟：回收租约过期的通知/邮件投递 claim（worker 崩溃后安全重领）。

    已成功投递的记录不会再次产生外部副作用（幂等 claim + 状态机）。
    """
    _record_beat_heartbeat()
    from datetime import timedelta

    from app.models.legal_notifications import LegalNotificationEvent
    from app.services.notification.notification_service import STATUS_PENDING, notification_service

    db = SessionLocal()
    try:
        from app.services.notification.outbound_email_service import outbound_email_service

        email_reclaimed = outbound_email_service.reclaim_stale(db=db)
        settings = get_settings()
        stale_before = utc_now() - timedelta(seconds=settings.NOTIFICATION_CLAIM_TTL_SECONDS)
        stale = db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.status == "sending",
            LegalNotificationEvent.claim_expires_at.isnot(None),
            LegalNotificationEvent.claim_expires_at < stale_before,
        ).limit(200).all()
        reclaimed = 0
        for ev in stale:
            notification_service.transition(db=db, event=ev, to=STATUS_PENDING,
                                             reason="lease_expired")
            ev.claim_expires_at = None
            ev.claimed_by = None
            reclaimed += 1
        db.commit()
        return {"email_reclaimed": email_reclaimed, "notification_reclaimed": reclaimed}
    finally:
        db.close()


@celery_app.task(name="retry_failed_webhook_deliveries")
@_beat_lock(task_name="retry_failed_webhook_deliveries", ttl_seconds=600)
def retry_failed_webhook_deliveries_task():
    """每5分钟：投递 pending Webhook，并对可重试失败做指数退避。"""
    _record_beat_heartbeat()
    import hashlib
    import hmac
    from app.models.legal_platform import WebhookDelivery, DeveloperApp
    from app.core.observability_sanitizer import redact_error

    db = SessionLocal()
    now = utc_now()
    retried = 0
    try:
        pending = db.query(WebhookDelivery).filter(
            WebhookDelivery.status.in_(("pending", "failed")),
            WebhookDelivery.attempt_count < 3,
        ).all()

        for delivery in pending:
            app_obj = db.query(DeveloperApp).filter(
                DeveloperApp.id == delivery.app_id,
                DeveloperApp.status == "active",
            ).first()
            if not app_obj or not app_obj.webhook_url:
                continue

            # A hash cannot sign a payload. Legacy applications must rotate the
            # secret once so the encrypted signing value is available.
            if not app_obj.webhook_secret_ciphertext:
                delivery.status = "failed"
                delivery.response_body_snippet = "Webhook signing secret must be rotated"
                continue

            backoff = 30 * (4 ** delivery.attempt_count)
            if delivery.last_attempted_at:
                from datetime import timedelta
                next_try = delivery.last_attempted_at + timedelta(seconds=backoff)
                if next_try.tzinfo:
                    next_try = next_try.replace(tzinfo=None)  # 与 naive 列/utc_now 一致
                if next_try > now:
                    continue

            delivery.attempt_count += 1
            delivery.last_attempted_at = now

            payload = {
                "event_type": delivery.event_type,
                "event_id": delivery.event_id,
            }
            payload_bytes = json.dumps(payload, separators=(",", ":")).encode()

            headers = {
                "Content-Type": "application/json",
                "X-Event-Type": delivery.event_type,
                "X-Event-Id": delivery.event_id,
            }
            # HMAC-SHA256 签名
            sig = hmac.new(
                app_obj.webhook_secret_ciphertext.encode("utf-8"),
                payload_bytes,
                hashlib.sha256,
            ).hexdigest()
            headers["X-Signature"] = f"sha256={sig}"

            try:
                import httpx
                from app.core.external_resilience import external_resilience

                def _post() -> httpx.Response:
                    resp = httpx.post(
                        app_obj.webhook_url,
                        content=payload_bytes,
                        headers=headers,
                        timeout=5.0,
                    )
                    resp.raise_for_status()
                    return resp

                # 韧性层：SSRF 校验 + 连接/5xx 重试；写超时 AMBIGUOUS 不盲目重试（跨 beat 状态机继续退避）。
                resp = external_resilience.call(
                    _post, service="webhook", op="deliver",
                    connector_id=app_obj.id, method="POST",
                    url=app_obj.webhook_url,  # P1-D：URL 来自 DB，出站前 SSRF 校验
                )
                delivery.response_status = resp.status_code
                delivery.response_body_snippet = resp.text[:512] if resp.text else None
                delivery.status = "success"
                retried += 1
            except Exception as exc:
                delivery.response_body_snippet = redact_error(exc)[:512]

        db.commit()
        return {"retried": retried}
    finally:
        db.close()
