"""运营看板与安全审计子路由：运营摘要、审计事件列表/导出/完整性校验/导出下载。"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, verify_org_role_access
from app.core.database import get_db
from app.models.legal_notifications import SecurityAuditEvent
from app.models.legal_platform import DeveloperApiUsage, LegalAsyncJob, WebhookDelivery
from app.models.org import LegalMemberRole
from app.models.user import User

router = APIRouter()

@router.get("/orgs/{org_id}/operations/summary")
def get_operations_summary(
    org_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """管理员运营看板的最小可信指标，全部来自持久化记录。"""
    verify_org_role_access(org_id, current_user.id, LegalMemberRole.admin, db)
    from sqlalchemy import func
    jobs = db.query(LegalAsyncJob).filter(LegalAsyncJob.organization_id == org_id)
    deliveries = db.query(WebhookDelivery).filter(WebhookDelivery.organization_id == org_id)
    usage = db.query(DeveloperApiUsage).filter(DeveloperApiUsage.organization_id == org_id)
    return {
        "queued_jobs": jobs.filter(LegalAsyncJob.status == "queued").count(),
        "failed_jobs": jobs.filter(LegalAsyncJob.status == "failed").count(),
        "failed_webhooks": deliveries.filter(WebhookDelivery.status == "failed").count(),
        "pending_webhooks": deliveries.filter(WebhookDelivery.status == "pending").count(),
        "api_calls": usage.with_entities(func.coalesce(func.sum(DeveloperApiUsage.call_count), 0)).scalar(),
        "callback_verification_failures": db.query(SecurityAuditEvent).filter(
            SecurityAuditEvent.organization_id == org_id,
            SecurityAuditEvent.event_type == "sign_callback",
            SecurityAuditEvent.result != "success",
        ).count(),
    }


@router.get("/orgs/{org_id}/security-audit")
def list_audit_events(
    org_id: int,
    event_type: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    verify_org_role_access(org_id, current_user.id, LegalMemberRole.admin, db)
    q = db.query(SecurityAuditEvent).filter(SecurityAuditEvent.organization_id == org_id)
    if event_type:
        q = q.filter(SecurityAuditEvent.event_type == event_type)
    if start:
        q = q.filter(SecurityAuditEvent.occurred_at >= start)
    if end:
        q = q.filter(SecurityAuditEvent.occurred_at <= end)
    total = q.count()
    items = q.order_by(SecurityAuditEvent.occurred_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {
        "items": [
            {
                "id": e.id,
                "seq_no": e.seq_no,
                "audit_id": e.audit_id,
                "event_type": e.event_type,
                "action": e.action,
                "actor_type": e.actor_type,
                "actor_id": e.actor_id,
                "result": e.result,
                "decision": e.decision,
                "reason_code": e.reason_code,
                "target_type": e.target_type,
                "target_id": e.target_id,
                "resource_version": e.resource_version,
                "trace_id": e.trace_id,
                "request_id": e.request_id,
                "schema_version": e.schema_version,
                "archived_at": e.archived_at,
                "occurred_at": e.occurred_at,
            }
            for e in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/orgs/{org_id}/security-audit/export")
def export_audit_events(
    org_id: int,
    event_type: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    fmt: str = "json",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """同步导出（≤1000 条）或创建异步任务（>1000 条）。导出行为本身写入安全审计。"""
    member = verify_org_role_access(org_id, current_user.id, LegalMemberRole.admin, db)
    from app.models.legal_platform import LegalAsyncJob
    from app.services.org.security_audit_service import verify_chain, write_event

    integrity = verify_chain(organization_id=org_id)
    if not integrity["intact"]:
        write_event(event_type="export", actor_type="user", actor_id=str(member.user_id),
                    result="blocked", organization_id=org_id, target_type="security_audit_events",
                    target_id="integrity_failure", db=db)
        raise HTTPException(423, detail="安全审计链校验失败，导出已冻结")

    q = db.query(SecurityAuditEvent).filter(SecurityAuditEvent.organization_id == org_id)
    if event_type:
        q = q.filter(SecurityAuditEvent.event_type == event_type)
    if start:
        q = q.filter(SecurityAuditEvent.occurred_at >= start)
    if end:
        q = q.filter(SecurityAuditEvent.occurred_at <= end)
    total = q.count()

    write_event(
        event_type="export", actor_type="user", actor_id=str(member.user_id),
        result="success", organization_id=org_id, target_type="security_audit_events",
        target_id=f"count:{total}", db=db,
    )

    if total > 1000:
        import json as _json

        from fastapi.responses import JSONResponse

        from app.core.error_codes import EXPORT_ASYNC_REQUIRED, err

        job = LegalAsyncJob(
            organization_id=org_id, job_type="audit_export", status="queued",
            created_by=member.user_id,
            input_json=_json.dumps({
                "organization_id": org_id,
                "event_type": event_type,
                "start": start,
                "end": end,
            }, ensure_ascii=False),
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        # 入队异步导出任务（此前缺失的断链：任务注册了但从未被调度）。
        try:
            from app.core.obs_context import get_context
            from app.tasks.ops_tasks import run_audit_export
            run_audit_export.delay(job.id, headers=get_context().as_headers())
        except Exception:  # noqa: BLE001 - worker 不可用时 beat/管理员可重试，不丢 job
            pass

        from app.services.jobs.async_job_service import serialize_job
        payload = serialize_job(job)
        payload["message"] = err(EXPORT_ASYNC_REQUIRED)["message"]
        return JSONResponse(status_code=202, content=payload)

    events = q.order_by(SecurityAuditEvent.occurred_at).all()
    rows = [
        {"seq_no": e.seq_no, "event_type": e.event_type, "actor_type": e.actor_type,
         "actor_id": e.actor_id, "result": e.result,
         "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None}
        for e in events
    ]
    if fmt == "csv":
        import csv
        import io
        buf = io.StringIO()
        if rows:
            writer = csv.DictWriter(buf, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                                 headers={"Content-Disposition": "attachment; filename=audit.csv"})
    return {"total": total, "events": rows}


@router.get("/orgs/{org_id}/security-audit/integrity-check")
def audit_integrity_check(
    org_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """管理员触发哈希链完整性校验（断链/重复/时间异常检测）。"""
    verify_org_role_access(org_id, current_user.id, LegalMemberRole.admin, db)
    from app.services.org.security_audit_service import verify_chain
    result = verify_chain(organization_id=org_id)
    return result


@router.get("/orgs/{org_id}/security-audit/exports")
def list_audit_exports(
    org_id: int,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """审计导出任务列表（org admin）。"""
    verify_org_role_access(org_id, current_user.id, LegalMemberRole.admin, db)
    q = db.query(LegalAsyncJob).filter(
        LegalAsyncJob.organization_id == org_id,
        LegalAsyncJob.job_type == "audit_export",
    )
    total = q.count()
    jobs = (
        q.order_by(LegalAsyncJob.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "items": [
            {
                "job_id": job.id,
                "status": job.status,
                "result_summary": job.result_summary,
                "output_json": job.output_json,
                "error_summary": job.error_summary,
                "created_by": job.created_by,
                "created_at": job.created_at.isoformat() if job.created_at else None,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "ended_at": job.ended_at.isoformat() if job.ended_at else None,
            }
            for job in jobs
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/orgs/{org_id}/security-audit/exports/{job_id}/download")
def download_audit_export(
    org_id: int,
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """下载审计导出文件（org admin）。每次下载均写入安全审计（重复下载可审计）。"""
    import json as _json
    from pathlib import Path

    member = verify_org_role_access(org_id, current_user.id, LegalMemberRole.admin, db)
    from app.services.org.security_audit_service import write_event

    job = (
        db.query(LegalAsyncJob)
        .filter(
            LegalAsyncJob.id == job_id,
            LegalAsyncJob.organization_id == org_id,
            LegalAsyncJob.job_type == "audit_export",
        )
        .first()
    )
    if job is None or job.status != "succeeded" or not job.output_json:
        raise HTTPException(404, detail="导出任务不存在或未完成")
    try:
        output = _json.loads(job.output_json)
        file_path = Path(output["file"])
    except (TypeError, ValueError, KeyError):
        raise HTTPException(500, detail="导出产物元数据损坏")  # noqa: B904
    if not file_path.exists():
        raise HTTPException(410, detail="导出文件已过期或被清理")

    # 下载行为审计（export 类失败为 block → fail-closed）
    try:
        write_event(
            event_type="export", actor_type="user", actor_id=str(member.user_id),
            result="success", organization_id=org_id, target_type="security_audit_events",
            target_id=f"download:{job_id}", action="audit_export_download",
            reason_code="audit_export_download", db=db,
        )
    except Exception as exc:  # noqa: BLE001 - 审计写失败按策略处理
        db.rollback()
        raise HTTPException(503, detail="审计事件写入失败，下载已冻结") from exc

    from fastapi.responses import FileResponse
    return FileResponse(
        file_path,
        media_type="application/x-ndjson",
        filename=file_path.name,
    )

