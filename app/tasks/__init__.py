import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.observability import log_async_task_event
from app.core.observability_sanitizer import sanitize_background_error_message
from app.core.time import utc_now
from app.models.document import Document, DocumentChunk
from app.models.user import User
from app.services.analysis_service import analysis_service
from app.services.document_job_service import document_job_service
from app.services.document_service import (
    DOCUMENT_STATUS_INDEXED,
    DOCUMENT_STATUS_PARSED,
    DocumentParsePermanentError,
    _build_embedding_id,
    _extract_segments,
    _split_text,
    _try_index_document,
    UPLOAD_DIR,
)
from app.services.storage_service import storage_service
from app.tasks.runtime import background_error_detail as _background_error_detail
from app.tasks.runtime import record_beat_heartbeat as _record_beat_heartbeat
from app.tasks.task_retry import retry_task as _retry_task_impl


def _retry_task(self, exc: Exception, **kwargs):
    return _retry_task_impl(
        self,
        exc,
        log_event=log_async_task_event,
        session_factory=SessionLocal,
        document_jobs=document_job_service,
        **kwargs,
    )


@celery_app.task(bind=True, name="parse_document")
def parse_document_task(self, document_id: int, file_path: str, file_type: str, snapshot_id: str | None = None):
    """异步解析文档：提取文本 → 切分 → 向量化入库"""
    db = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            return {"status": "error", "message": "Document not found"}

        # 长流程权限快照：硬撤销（禁用/强制退出/授权撤销）立即终止。
        if snapshot_id:
            from app.services.authorization_service import authorization_service
            authorization_service.assert_snapshot(db, snapshot_id, user_id=doc.user_id)

        log_async_task_event(
            user_id=doc.user_id,
            module="async_task",
            action="document_parse_started",
            target_type="document",
            target_id=document_id,
            detail=f"task_id={self.request.id}",
        )
        document_job_service.mark_started(
            self.request.id,
            db,
            current_step="extracting",
            message="正在提取文档文本",
            progress=10,
        )

        # 更新状态为解析中
        doc.status = "processing"
        db.commit()
        self.update_state(state="PROCESSING", meta={"step": "extracting"})

        # 提取文本
        segments = _extract_segments(file_path, file_type)

        self.update_state(state="PROCESSING", meta={"step": "splitting"})
        document_job_service.update_progress(
            self.request.id,
            db,
            current_step="splitting",
            message="正在切分文档内容",
            progress=45,
        )

        # 切分
        chunks = _split_text(segments)

        # 写入 chunks 表
        db_chunks = []
        for chunk in chunks:
            db_chunks.append(
                DocumentChunk(
                    document_id=document_id,
                    chunk_index=chunk["chunk_index"],
                    content=chunk["content"],
                    page_number=chunk.get("page_number"),
                    section_title=chunk.get("section_title"),
                    section_path=" > ".join(chunk.get("section_path") or []),
                    segment_type=chunk.get("segment_type"),
                    table_like=bool(chunk.get("table_like")),
                    visual_tags=" ".join(chunk.get("visual_tags") or []),
                    ocr_quality=chunk.get("ocr_quality"),
                    embedding_id=_build_embedding_id(document_id, chunk["chunk_index"]),
                )
            )
        db.add_all(db_chunks)
        db.commit()

        self.update_state(state="PROCESSING", meta={"step": "indexing", "total_chunks": len(chunks)})
        document_job_service.update_progress(
            self.request.id,
            db,
            current_step="indexing",
            message=f"正在建立索引，共 {len(chunks)} 个分片",
            progress=75,
        )

        # 向量化入库
        index_error = _try_index_document(
            document_id, chunks, user_id=doc.user_id,
            knowledge_base_id=getattr(doc, "knowledge_base_id", None),
        )

        # 更新状态为已完成
        doc.status = DOCUMENT_STATUS_INDEXED if index_error is None else DOCUMENT_STATUS_PARSED
        db.commit()

        log_async_task_event(
            user_id=doc.user_id,
            module="async_task",
            action="document_parse_succeeded",
            target_type="document",
            target_id=document_id,
            detail=f"task_id={self.request.id}; chunks={len(chunks)}; indexed={index_error is None}",
        )
        if index_error is not None:
            log_async_task_event(
                user_id=doc.user_id,
                module="async_task",
                action="document_index_degraded",
                target_type="document",
                target_id=document_id,
                detail=_background_error_detail(self.request.id),
            )
        document_job_service.mark_succeeded(
            self.request.id,
            db,
            message="文档解析完成，索引已降级" if index_error is not None else "文档解析完成",
            result_summary=(
                f"共切分 {len(chunks)} 个文档片段，但索引失败：任务执行失败，请查看系统日志"
                if index_error is not None
                else f"共切分 {len(chunks)} 个文档片段并完成索引"
            ),
        )

        return {
            "status": "success",
            "document_id": document_id,
            "chunks": len(chunks),
            "indexed": index_error is None,
            "index_error": sanitize_background_error_message(str(index_error)) if index_error is not None else None,
        }

    except DocumentParsePermanentError as e:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if doc:
            doc.status = "failed"
            db.commit()
            log_async_task_event(
                user_id=doc.user_id,
                module="async_task",
                action="document_parse_failed",
                target_type="document",
                target_id=document_id,
                detail=_background_error_detail(self.request.id),
            )
            document_job_service.mark_failed(
                self.request.id,
                db,
                error_message=sanitize_background_error_message(str(e)),
                message="文档解析失败",
                retry_count=int(getattr(self.request, "retries", 0) or 0),
            )
        return {
            "status": "error",
            "document_id": document_id,
            "message": sanitize_background_error_message(str(e)),
        }
    except Exception as e:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if doc:
            doc.status = "failed"
            db.commit()
            _retry_task(
                self,
                e,
                user_id=doc.user_id,
                target_type="document",
                target_id=document_id,
                action_prefix="document_parse",
            )
        raise
    finally:
        db.close()


@celery_app.task(bind=True, name="summarize_document")
def summarize_document_task(self, document_id: int, user_id: int, max_length: int = 500, snapshot_id: str | None = None):
    db = SessionLocal()
    try:
        # 长流程权限快照：硬撤销立即终止。
        if snapshot_id:
            from app.services.authorization_service import authorization_service
            authorization_service.assert_snapshot(db, snapshot_id, user_id=user_id)

        log_async_task_event(
            user_id=user_id,
            module="async_task",
            action="document_summary_started",
            target_type="document",
            target_id=document_id,
            detail=f"task_id={self.request.id}; max_length={max_length}",
        )
        document_job_service.mark_started(
            self.request.id,
            db,
            current_step="loading_document",
            message="正在加载文档内容",
            progress=15,
        )
        self.update_state(state="PROCESSING", meta={"step": "loading_document"})
        from app.services.document_service import document_service

        raw_text = document_service.summarize(document_id, db, user_id=user_id)

        self.update_state(state="PROCESSING", meta={"step": "summarizing"})
        document_job_service.update_progress(
            self.request.id,
            db,
            current_step="summarizing",
            message="正在生成文档摘要",
            progress=65,
        )
        summary = asyncio.run(analysis_service.summarize_document(raw_text, max_length=max_length))

        doc = document_service.get(document_id, db, user_id=user_id)
        if doc:
            doc.summary = summary
            db.commit()

        log_async_task_event(
            user_id=user_id,
            module="async_task",
            action="document_summary_succeeded",
            target_type="document",
            target_id=document_id,
            detail=f"task_id={self.request.id}",
        )
        document_job_service.mark_succeeded(
            self.request.id,
            db,
            message="文档摘要完成",
            result_summary=(summary or "")[:500],
        )
        return {"document_id": document_id, "summary": summary}
    except Exception as e:
        _retry_task(
            self,
            e,
            user_id=user_id,
            target_type="document",
            target_id=document_id,
            action_prefix="document_summary",
        )
    finally:
        db.close()


@celery_app.task(bind=True, name="analyze_document")
def analyze_document_task(self, document_id: int, user_id: int, max_length: int = 500, snapshot_id: str | None = None):
    db = SessionLocal()
    try:
        # 长流程权限快照：硬撤销立即终止。
        if snapshot_id:
            from app.services.authorization_service import authorization_service
            authorization_service.assert_snapshot(db, snapshot_id, user_id=user_id)

        log_async_task_event(
            user_id=user_id,
            module="async_task",
            action="document_analysis_started",
            target_type="document",
            target_id=document_id,
            detail=f"task_id={self.request.id}; max_length={max_length}",
        )
        document_job_service.mark_started(
            self.request.id,
            db,
            current_step="loading_document",
            message="正在加载文档内容",
            progress=15,
        )
        self.update_state(state="PROCESSING", meta={"step": "loading_document"})
        from app.services.document_service import document_service

        document_job_service.update_progress(
            self.request.id,
            db,
            current_step="analyzing",
            message="正在分析摘要、风险、待办和条款",
            progress=60,
        )
        result = asyncio.run(
            document_service.analyze(
                document_id=document_id,
                db=db,
                user_id=user_id,
                max_length=max_length,
            )
        )
        log_async_task_event(
            user_id=user_id,
            module="async_task",
            action="document_analysis_succeeded",
            target_type="document",
            target_id=document_id,
            detail=f"task_id={self.request.id}; analysis_status={(result or {}).get('analysis_status', 'success')}",
        )
        result_summary = (result.get("summary") or "")[:500] if isinstance(result, dict) else None
        document_job_service.mark_succeeded(
            self.request.id,
            db,
            message="文档分析完成（部分降级）" if isinstance(result, dict) and result.get("analysis_status") == "partial" else "文档分析完成",
            result_summary=result_summary,
        )
        return result
    except Exception as e:
        _retry_task(
            self,
            e,
            user_id=user_id,
            target_type="document",
            target_id=document_id,
            action_prefix="document_analysis",
        )
    finally:
        db.close()


@celery_app.task(name="dispatch_operational_alerts")
def dispatch_operational_alerts_task():
    _record_beat_heartbeat()
    db = SessionLocal()
    try:
        from app.services.operational_alert_service import operational_alert_service

        return operational_alert_service.dispatch(db=db)
    finally:
        db.close()


@celery_app.task(name="run_database_archive")
def run_database_archive_task():
    """按表保留策略批量清理过期日志/用量记录。

    默认关闭且 dry-run；DATABASE_ARCHIVE_ENABLED=true 且 DRY_RUN=false 才真实删除。
    使用统一事务上下文 session_scope；慢 SQL 日志通过 correlation id 关联到本任务。
    """
    _record_beat_heartbeat()
    from app.core.database import session_scope
    from app.core.db_monitor import set_db_correlation_id

    run_key = f"archive-{utc_now().strftime('%Y%m%dT%H%M%S')}"
    set_db_correlation_id(run_key)
    try:
        with session_scope() as db:
            from app.services.archive_service import archive_service
            from app.services.idempotency_service import idempotency_service

            result = archive_service.run(db=db)
            # 幂等键 TTL 清理（分批、幂等，可随归档任务安全执行）
            result["expired_idempotency_keys_deleted"] = idempotency_service.cleanup_expired(db)
            return result
    finally:
        set_db_correlation_id(None)


@celery_app.task(name="check_legal_deadline_reminders")
def check_legal_deadline_reminders_task():
    """每15分钟：扫描需要发送提醒的案件关键日期，写入通知事件（同一日期/渠道/偏移只发一次）。"""
    _record_beat_heartbeat()
    from app.models.legal_portal import LegalDeadline
    from app.models.legal_notifications import LegalNotificationEvent
    import json

    db = SessionLocal()
    now = utc_now()
    created = 0
    try:
        active_deadlines = db.query(LegalDeadline).filter(
            LegalDeadline.status == "active",
        ).all()

        for dl in active_deadlines:
            offsets = json.loads(dl.reminder_offsets_json or "[7,3,1]")
            for offset_days in offsets:
                from datetime import timedelta
                remind_at = dl.deadline_at - timedelta(days=offset_days)
                if remind_at.tzinfo:
                    remind_at = remind_at.replace(tzinfo=None)  # 与 naive 列/utc_now 一致
                if remind_at > now:
                    continue
                # 幂等：同一 deadline + offset 不重复
                dedupe_key = f"deadline:{dl.id}:offset:{offset_days}"
                exists = db.query(LegalNotificationEvent).filter(
                    LegalNotificationEvent.reference_type == "deadline",
                    LegalNotificationEvent.reference_id == dl.id,
                    LegalNotificationEvent.body == dedupe_key,
                ).first()
                if exists:
                    continue

                event = LegalNotificationEvent(
                    organization_id=dl.organization_id,
                    user_id=dl.owner_id,
                    case_id=dl.case_id,
                    event_type="deadline_reminder",
                    title=f"关键日期提醒：{dl.deadline_type}（提前{offset_days}天）",
                    body=dedupe_key,
                    channel="site",
                    status="pending",
                    reference_type="deadline",
                    reference_id=dl.id,
                    scheduled_at=remind_at,
                )
                db.add(event)
                created += 1

        db.commit()
        return {"created_reminders": created}
    finally:
        db.close()


@celery_app.task(name="scan_overdue_invoices")
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


@celery_app.task(name="scan_expired_portal_links")
def scan_expired_portal_links_task():
    """每小时：将已过 expires_at 的门户链接置为 expired，并通知创建律师。

    同一链接只通知一次（reference_type=portal_link + body 去重键）：
    - 过期（active→expired）：portal_link:{id}:expired
    - 即将到期（3 天内）：portal_link:{id}:expiring_soon
    """
    _record_beat_heartbeat()
    from datetime import timedelta
    from app.models.legal import LegalCase
    from app.models.legal_portal import LegalPortalLink
    from app.models.legal_notifications import LegalNotificationEvent

    db = SessionLocal()
    now = utc_now()

    def _notify_once(link: LegalPortalLink, dedupe_key: str, title_factory) -> int:
        exists = db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.reference_type == "portal_link",
            LegalNotificationEvent.reference_id == link.id,
            LegalNotificationEvent.body == dedupe_key,
        ).first()
        if exists:
            return 0
        case_title = db.query(LegalCase.title).filter(LegalCase.id == link.case_id).scalar()
        db.add(LegalNotificationEvent(
            organization_id=link.organization_id,
            user_id=link.created_by,
            case_id=link.case_id,
            event_type="portal",
            title=title_factory(case_title),
            body=dedupe_key,
            channel="site",
            status="delivered",
            sent_at=now,
            reference_type="portal_link",
            reference_id=link.id,
        ))
        return 1

    try:
        expired_links = db.query(LegalPortalLink).filter(
            LegalPortalLink.status == "active",
            LegalPortalLink.is_permanent == 0,
            LegalPortalLink.expires_at.isnot(None),
            LegalPortalLink.expires_at < now,
        ).all()
        expired_count = 0
        expired_notified = 0
        for link in expired_links:
            link.status = "expired"
            expired_count += 1
            expired_notified += _notify_once(
                link,
                f"portal_link:{link.id}:expired",
                lambda t: f"客户门户链接已到期：{t or f'案件#{link.case_id}'}",
            )

        expiring_soon = db.query(LegalPortalLink).filter(
            LegalPortalLink.status == "active",
            LegalPortalLink.is_permanent == 0,
            LegalPortalLink.expires_at.isnot(None),
            LegalPortalLink.expires_at > now,
            LegalPortalLink.expires_at <= now + timedelta(days=3),
        ).all()
        expiring_notified = 0
        for link in expiring_soon:
            days_left = max(1, (link.expires_at - now).days)
            expiring_notified += _notify_once(
                link,
                f"portal_link:{link.id}:expiring_soon",
                lambda t: f"门户链接即将到期（{days_left} 天内）：{t or f'案件#{link.case_id}'}",
            )

        db.commit()
        return {
            "expired_links": expired_count,
            "expired_notified": expired_notified,
            "expiring_notified": expiring_notified,
        }
    finally:
        db.close()


@celery_app.task(name="scan_expired_subscriptions")
def scan_expired_subscriptions_task():
    """每小时：将已过 current_period_end 的 active 订阅置为 expired（配额回落免费版）。"""
    _record_beat_heartbeat()
    from app.services.subscription_service import subscription_service

    db = SessionLocal()
    try:
        return {"expired_subscriptions": subscription_service.expire_overdue_subscriptions(db)}
    finally:
        db.close()


@celery_app.task(name="scan_contract_expiry_alerts")
def scan_contract_expiry_alerts_task():
    """每天：扫描已确认的合同里程碑，提前90/30/7天各创建一次通知事件。"""
    _record_beat_heartbeat()
    from app.models.legal_contract import LegalContractMilestone
    from app.models.legal_notifications import LegalNotificationEvent
    from app.models.legal_contract import LegalContract
    import json
    from datetime import timedelta

    db = SessionLocal()
    now = utc_now()
    created = 0
    try:
        milestones = db.query(LegalContractMilestone).filter(
            LegalContractMilestone.status == "confirmed",
            LegalContractMilestone.standard_date.isnot(None),
        ).all()

        for ms in milestones:
            contract = db.query(LegalContract).filter(LegalContract.id == ms.contract_id).first()
            if not contract or contract.status in ("terminated", "expired", "voided"):
                continue
            for offset_days in [90, 30, 7]:
                remind_at = ms.standard_date - timedelta(days=offset_days)
                if remind_at.tzinfo:
                    remind_at = remind_at.replace(tzinfo=None)  # 与 naive 列/utc_now 一致
                if remind_at > now:
                    continue
                dedupe_key = f"milestone:{ms.id}:offset:{offset_days}"
                exists = db.query(LegalNotificationEvent).filter(
                    LegalNotificationEvent.reference_type == "contract_milestone",
                    LegalNotificationEvent.reference_id == ms.id,
                    LegalNotificationEvent.body == dedupe_key,
                ).first()
                if exists:
                    continue

                responsible_id = contract.responsible_user_id or contract.created_by
                event = LegalNotificationEvent(
                    organization_id=ms.organization_id,
                    user_id=responsible_id,
                    event_type="contract_expiry_alert",
                    title=f"合同到期预警：{contract.title}（{ms.milestone_type}，提前{offset_days}天）",
                    body=dedupe_key,
                    channel="site",
                    status="pending",
                    reference_type="contract_milestone",
                    reference_id=ms.id,
                    scheduled_at=remind_at,
                )
                db.add(event)
                created += 1

        db.commit()
        return {"created_alerts": created}
    finally:
        db.close()


@celery_app.task(name="dispatch_notification_events")
def dispatch_notification_events_task():
    """每60秒：投递已到提醒时间的 pending 通知事件。

    站内通知标记为 delivered（进入铃铛未读），邮件渠道走 OutboundEmailService。
    尚未到 scheduled_at 的事件跳过，到点后由后续 tick 投递。
    """
    _record_beat_heartbeat()
    from app.services.notification_service import notification_service

    db = SessionLocal()
    try:
        return notification_service.dispatch_pending(db=db)
    finally:
        db.close()


@celery_app.task(name="retry_failed_webhook_deliveries")
def retry_failed_webhook_deliveries_task():
    """每5分钟：对失败次数 < 3 的 Webhook 投递进行指数退避重试（含 HMAC-SHA256 签名头）。"""
    _record_beat_heartbeat()
    import hashlib, hmac, json
    from app.models.legal_platform import WebhookDelivery, DeveloperApp

    db = SessionLocal()
    now = utc_now()
    retried = 0
    try:
        pending = db.query(WebhookDelivery).filter(
            WebhookDelivery.status == "failed",
            WebhookDelivery.attempt_count < 3,
        ).all()

        for delivery in pending:
            app_obj = db.query(DeveloperApp).filter(
                DeveloperApp.id == delivery.app_id,
                DeveloperApp.status == "active",
            ).first()
            if not app_obj or not app_obj.webhook_url:
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
            if app_obj.webhook_secret_hash:
                sig = hmac.new(
                    app_obj.webhook_secret_hash.encode(),
                    payload_bytes,
                    hashlib.sha256,
                ).hexdigest()
                headers["X-Signature"] = f"sha256={sig}"

            try:
                import httpx
                resp = httpx.post(
                    app_obj.webhook_url,
                    content=payload_bytes,
                    headers=headers,
                    timeout=5.0,
                )
                delivery.response_status = resp.status_code
                delivery.response_body_snippet = resp.text[:512] if resp.text else None
                if resp.status_code < 400:
                    delivery.status = "success"
                    retried += 1
            except Exception as exc:
                delivery.response_body_snippet = str(exc)[:512]

        db.commit()
        return {"retried": retried}
    finally:
        db.close()


@celery_app.task(name="process_open_contract_review")
def process_open_contract_review_task(job_id: int):
    """消费开放合同审查任务，绝不把合同正文写回任务结果或日志。"""
    from app.models.legal_platform import LegalAsyncJob, LegalAsyncJobInput

    db = SessionLocal()
    try:
        job = db.query(LegalAsyncJob).filter(
            LegalAsyncJob.id == job_id, LegalAsyncJob.job_type == "open_contract_review"
        ).first()
        if not job or job.status in ("succeeded", "processing"):
            return {"skipped": True}
        source = db.query(LegalAsyncJobInput).filter(LegalAsyncJobInput.job_id == job.id).first()
        if not source:
            job.status = "failed"; job.error_summary = "受控输入不存在"; job.ended_at = utc_now()
            db.commit(); return {"failed": True}
        job.status = "processing"; job.started_at = utc_now(); job.progress = 10; db.commit()
        content = source.content_ciphertext or ""
        # 可预测的最小审查摘要；实际模型审查可替换该消费者，不改变状态契约。
        flags = [word for word in ("违约", "赔偿", "争议", "保密", "期限") if word in content]
        job.result_summary = json.dumps({"title": source.title, "risk_keywords": flags, "content_length": len(content)}, ensure_ascii=False)
        job.status = "succeeded"; job.progress = 100; job.ended_at = utc_now()
        db.commit()
        return {"succeeded": True}
    except Exception:
        db.rollback()
        job = db.query(LegalAsyncJob).filter(LegalAsyncJob.id == job_id).first()
        if job:
            job.status = "failed"; job.retry_count = (job.retry_count or 0) + 1
            job.error_summary = "合同审查任务处理失败，可重试"; job.ended_at = utc_now()
            db.commit()
        raise
    finally:
        db.close()


@celery_app.task(name="parse_contract_versions")
def parse_contract_versions_task():
    """每5分钟：扫描 parse_status=uploading 的合同版本，提取条款写入 legal_contract_clauses。"""
    _record_beat_heartbeat()
    from app.models.legal_contract import LegalContractVersion, LegalContractClause
    from app.models.legal_platform import LegalAsyncJob

    db = SessionLocal()
    processed = 0
    try:
        # 陈旧重扫：parsing 状态超过 15 分钟视为 worker 崩溃残留，重新解析；
        # 活跃 parsing（updated_at 较新）不会被重复拾取
        from datetime import timedelta
        stale_cutoff = utc_now() - timedelta(minutes=15)
        pending = db.query(LegalContractVersion).filter(
            LegalContractVersion.parse_status == "uploading",
        ).limit(20).all()
        pending += db.query(LegalContractVersion).filter(
            LegalContractVersion.parse_status == "parsing",
            LegalContractVersion.updated_at < stale_cutoff,
        ).limit(20).all()

        for ver in pending:
            # 更新为解析中
            ver.parse_status = "parsing"
            db.commit()

            job = LegalAsyncJob(
                organization_id=ver.organization_id,
                resource_type="contract_version",
                resource_id=ver.id,
                job_type="contract_parse",
                status="processing",
                created_by=ver.created_by,
            )
            db.add(job)
            db.flush()

            try:
                text = ver.text_snapshot or ""
                if not text.strip():
                    ver.parse_status = "failed"
                    job.status = "failed"
                    job.error_summary = "无文本内容可解析"
                    db.commit()
                    continue

                # 简单段落拆分为条款（按空行或"第X条"拆分）
                import re
                clauses = re.split(r'\n(?=第[零一二三四五六七八九十百千万\d]+条)', text)
                if len(clauses) < 2:
                    clauses = [p for p in text.split("\n\n") if p.strip()]

                db.query(LegalContractClause).filter(
                    LegalContractClause.contract_version_id == ver.id
                ).delete()

                for seq, clause_text in enumerate(clauses):
                    clause_text = clause_text.strip()
                    if not clause_text:
                        continue
                    m = re.match(r'^(第[零一二三四五六七八九十百千万\d]+条)\s*', clause_text)
                    clause_no = m.group(1) if m else None
                    db.add(LegalContractClause(
                        contract_version_id=ver.id,
                        clause_no=clause_no,
                        content=clause_text,
                        sequence=seq,
                        parse_confidence=0.75 if clause_no else 0.45,
                    ))

                total_clauses = len(clauses)
                avg_conf = 0.75 if total_clauses > 2 else 0.45

                from sqlalchemy import Numeric
                ver.parse_status = "ready" if avg_conf >= 0.7 else "needs_confirmation"
                ver.parse_confidence = avg_conf
                job.status = "succeeded"
                job.progress = 100
                job.result_summary = f"提取 {total_clauses} 条款，置信度 {avg_conf:.2f}"
                db.commit()
                processed += 1

            except Exception as exc:
                try:
                    ver.parse_status = "failed"
                    job.status = "failed"
                    job.error_summary = str(exc)[:200]
                    db.commit()
                except Exception:
                    pass

        return {"processed": processed}
    finally:
        db.close()


@celery_app.task(name="check_legal_approval_timeouts")
def check_legal_approval_timeouts_task():
    """Beat 任务：扫描超时审批步骤并标记为 timeout，推进审批链状态。"""
    _record_beat_heartbeat()
    from app.models.legal import LegalApprovalChain, LegalApprovalStep

    db = SessionLocal()
    now = utc_now()
    timed_out_steps = 0
    timed_out_chains = 0
    try:
        overdue = (
            db.query(LegalApprovalStep)
            .filter(
                LegalApprovalStep.status == "pending",
                LegalApprovalStep.due_at.isnot(None),
                LegalApprovalStep.due_at < now,
            )
            .all()
        )
        chain_ids: set[int] = set()
        for step in overdue:
            step.status = "timeout"
            step.acted_at = now
            chain_ids.add(step.chain_id)
            timed_out_steps += 1
        db.flush()

        for chain_id in chain_ids:
            chain = db.query(LegalApprovalChain).filter(
                LegalApprovalChain.id == chain_id,
                LegalApprovalChain.status == "in_progress",
            ).first()
            if not chain:
                continue
            steps_at_current = (
                db.query(LegalApprovalStep)
                .filter(
                    LegalApprovalStep.chain_id == chain_id,
                    LegalApprovalStep.step_order == chain.current_step,
                )
                .all()
            )
            # 当前步骤全部结束（非 pending）且含超时 → 整链超时
            all_done = all(s.status != "pending" for s in steps_at_current)
            any_timeout = any(s.status == "timeout" for s in steps_at_current)
            if all_done and any_timeout:
                chain.status = "timeout"
                timed_out_chains += 1

        db.commit()
        return {"timed_out_steps": timed_out_steps, "timed_out_chains": timed_out_chains}
    finally:
        db.close()


@celery_app.task(name="confirm_account_deletions")
def confirm_account_deletions_task():
    """Beat 任务：自动确认冷却期已满（默认30天）的账号注销请求，执行匿名化。"""
    _record_beat_heartbeat()
    db = SessionLocal()
    try:
        from app.services.account_deletion_service import confirm_expired_pending

        confirmed_count = confirm_expired_pending(db)
        return {"confirmed_count": confirmed_count}
    finally:
        db.close()


@celery_app.task(name="create_pilot_backup")
def create_pilot_backup_task():
    """Beat 任务：每日全量备份（数据库 + 本地数据目录），调度即授权 --confirm。

    仅支持 MySQL/PostgreSQL 驱动；sqlite（默认开发库）等驱动直接跳过。
    """
    _record_beat_heartbeat()
    settings = get_settings()
    database_url = settings.DATABASE_URL or ""
    driver = database_url.split(":", 1)[0]
    if not database_url or not driver.startswith(("mysql", "postgres")):
        return {"status": "skipped", "reason": f"unsupported_database_driver: {driver or 'empty'}"}
    script = Path(__file__).resolve().parents[2] / "scripts" / "create_pilot_backup.py"
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    command = [sys.executable, str(script), "--confirm", "--output-dir", settings.BACKUP_OUTPUT_DIR]
    for data_dir in settings.BACKUP_DATA_DIRS:
        command.extend(["--data-dir", data_dir])
    if settings.BACKUP_OFFSITE_DIR:
        command.extend(["--offsite-dir", settings.BACKUP_OFFSITE_DIR])
    command.extend(["--retention-count", str(settings.BACKUP_RETENTION_COUNT)])
    try:
        process = subprocess.run(command, capture_output=True, text=True, env=env, timeout=180)
    except (subprocess.SubprocessError, OSError) as exc:
        return {"status": "error", "message": sanitize_background_error_message(str(exc))}
    try:
        payload = json.loads(process.stdout or "{}")
    except json.JSONDecodeError:
        payload = {}
    if process.returncode == 0 and payload.get("status") == "ok":
        return {"status": "ok", "backup_dir": payload.get("backup_dir")}
    message = payload.get("message") or (process.stderr or "").strip() or f"exit_code={process.returncode}"
    return {"status": "error", "message": sanitize_background_error_message(message)}


@celery_app.task(name="dispatch_feishu_reminders")
def dispatch_feishu_reminders_task():
    """M4：向已绑定飞书用户推送激活引导/周报回访卡片（出站未配置凭据时自动跳过）。"""
    _record_beat_heartbeat()
    db = SessionLocal()
    try:
        import asyncio

        from app.services.feishu_service import dispatch_feishu_reminders

        return asyncio.run(dispatch_feishu_reminders(db))
    finally:
        db.close()
