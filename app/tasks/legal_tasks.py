"""法律（截止日期/门户链接/合同/审批/开放合同审查） 任务：从 app.tasks.__init__ 拆出（P3 上帝文件拆分）。"""
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


@celery_app.task(name="check_legal_deadline_reminders")
@_beat_lock(task_name="check_legal_deadline_reminders", ttl_seconds=1800)
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


@celery_app.task(name="scan_expired_portal_links")
@_beat_lock(task_name="scan_expired_portal_links", ttl_seconds=7200)
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


@celery_app.task(name="scan_contract_expiry_alerts")
@_beat_lock(task_name="scan_contract_expiry_alerts", ttl_seconds=86400)
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


# P1：queued 超过该阈值视为排队超时 → expired（Job 状态机增量值）。
_OPEN_REVIEW_QUEUED_EXPIRY_HOURS = 6


@celery_app.task(name="recover_queued_open_contract_reviews")
@_beat_lock(task_name="recover_queued_open_contract_reviews", ttl_seconds=600)
def recover_queued_open_contract_reviews_task():
    """Re-dispatch accepted Open API review jobs when the initial enqueue failed.

    超过陈旧阈值（_OPEN_REVIEW_QUEUED_EXPIRY_HOURS）的 queued 任务标记为
    expired（P1 Job 状态机），不再派发；未过期任务重新入队。
    """
    _record_beat_heartbeat()
    from app.models.legal_platform import LegalAsyncJob
    from datetime import timedelta

    db = SessionLocal()
    dispatched = 0
    expired = 0
    try:
        stale_before = utc_now() - timedelta(hours=_OPEN_REVIEW_QUEUED_EXPIRY_HOURS)
        stale_ids = [
            row[0]
            for row in db.query(LegalAsyncJob.id).filter(
                LegalAsyncJob.job_type == "open_contract_review",
                LegalAsyncJob.status == "queued",
                LegalAsyncJob.created_at < stale_before,
            ).limit(200).all()
        ]
        for job_id in stale_ids:
            job = db.query(LegalAsyncJob).filter(LegalAsyncJob.id == job_id).first()
            if job and job.status == "queued":
                job.status = "expired"
                job.ended_at = utc_now()
                job.error_summary = "任务排队超时，已过期"
                expired += 1
        if expired:
            db.commit()
        queued_ids = [
            row[0]
            for row in db.query(LegalAsyncJob.id).filter(
                LegalAsyncJob.job_type == "open_contract_review",
                LegalAsyncJob.status == "queued",
            ).order_by(LegalAsyncJob.created_at.asc()).limit(200).all()
        ]
        for job_id in queued_ids:
            try:
                process_open_contract_review_task.delay(job_id, headers=obs_enqueue_headers())
                dispatched += 1
            except Exception:  # noqa: BLE001 - retain queued state for the next beat run
                continue
        return {"dispatched": dispatched, "expired": expired}
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
        if job.cancel_requested:
            # 取消优先：已请求取消的任务不再执行（幂等取消语义）。
            job.status = "cancelled"
            job.ended_at = utc_now()
            job.error_summary = "任务已被取消"
            db.commit()
            return {"cancelled": True}
        source = db.query(LegalAsyncJobInput).filter(LegalAsyncJobInput.job_id == job.id).first()
        if not source:
            job.status = "failed"
            job.error_summary = "受控输入不存在"
            job.ended_at = utc_now()
            db.commit()
            return {"failed": True}
        job.status = "processing"
        job.started_at = utc_now()
        job.progress = 10
        db.commit()
        content = source.content_ciphertext or ""
        # 可预测的最小审查摘要；实际模型审查可替换该消费者，不改变状态契约。
        flags = [word for word in ("违约", "赔偿", "争议", "保密", "期限") if word in content]
        job.result_summary = json.dumps({"title": source.title, "risk_keywords": flags, "content_length": len(content)}, ensure_ascii=False)
        job.status = "succeeded"
        job.progress = 100
        job.ended_at = utc_now()
        db.commit()
        return {"succeeded": True}
    except Exception:
        db.rollback()
        job = db.query(LegalAsyncJob).filter(LegalAsyncJob.id == job_id).first()
        if job:
            job.status = "failed"
            job.retry_count = (job.retry_count or 0) + 1
            job.error_summary = "合同审查任务处理失败，可重试"
            job.ended_at = utc_now()
            db.commit()
        raise
    finally:
        db.close()


@celery_app.task(name="parse_contract_versions")
@_beat_lock(task_name="parse_contract_versions", ttl_seconds=600)
def parse_contract_versions_task():
    """每5分钟：扫描 parse_status=uploading 的合同版本，提取条款写入 legal_contract_clauses。"""
    _record_beat_heartbeat()
    from app.models.legal_contract import LegalContractVersion, LegalContractClause
    from app.models.legal_platform import LegalAsyncJob

    db = SessionLocal()
    processed = 0
    try:
        # 陈旧重扫：parsing 状态超过 15 分钟视为 worker 崩溃残留，重新解析；
        # 活跃 parsing（created_at 较新）不会被重复拾取。
        # 注：模型无 updated_at 列（契约测试暴露），按 created_at 判定陈旧语义。
        from datetime import timedelta
        stale_cutoff = utc_now() - timedelta(minutes=15)
        pending = db.query(LegalContractVersion).filter(
            LegalContractVersion.parse_status == "uploading",
        ).limit(20).all()
        pending += db.query(LegalContractVersion).filter(
            LegalContractVersion.parse_status == "parsing",
            LegalContractVersion.created_at < stale_cutoff,
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
@_beat_lock(task_name="check_legal_approval_timeouts", ttl_seconds=600)
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
