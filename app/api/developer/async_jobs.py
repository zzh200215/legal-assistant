"""法律异步任务子路由：org admin 的任务列表/详情/取消。"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, verify_org_role_access
from app.core.database import get_db
from app.core.error_codes import JOB_NOT_FOUND, err
from app.models.legal_platform import LegalAsyncJob
from app.models.org import LegalMemberRole
from app.models.user import User

router = APIRouter()

@router.get("/orgs/{org_id}/async-jobs")
def list_async_jobs(
    org_id: int,
    job_type: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    verify_org_role_access(org_id, current_user.id, LegalMemberRole.admin, db)
    q = db.query(LegalAsyncJob).filter(LegalAsyncJob.organization_id == org_id)
    if job_type:
        q = q.filter(LegalAsyncJob.job_type == job_type)
    if status:
        q = q.filter(LegalAsyncJob.status == status)
    total = q.count()
    items = q.order_by(LegalAsyncJob.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {
        "items": [
            {
                "job_id": job.id,
                "task_id": job.id,
                "job_type": job.job_type,
                "status": job.status,
                "progress": job.progress,
                "status_url": f"/api/developer/orgs/{org_id}/async-jobs/{job.id}",
                "retry_count": job.retry_count,
                "error_summary": job.error_summary,
                "result_summary": job.result_summary,
                "created_by": job.created_by,
                "created_at": job.created_at,
                "started_at": job.started_at,
                "ended_at": job.ended_at,
            }
            for job in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/orgs/{org_id}/async-jobs/{job_id}")
def get_async_job(
    org_id: int,
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """异步任务详情（org admin，按组织隔离）。"""
    verify_org_role_access(org_id, current_user.id, LegalMemberRole.admin, db)
    job = db.query(LegalAsyncJob).filter(
        LegalAsyncJob.id == job_id,
        LegalAsyncJob.organization_id == org_id,
    ).first()
    if not job:
        raise HTTPException(404, detail=err(JOB_NOT_FOUND))
    from app.services.jobs.async_job_service import serialize_job
    return serialize_job(job)


@router.post("/orgs/{org_id}/async-jobs/{job_id}/cancel")
def cancel_async_job(
    org_id: int,
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """取消异步任务（org admin；幂等：已终态返回当前状态）。"""
    verify_org_role_access(org_id, current_user.id, LegalMemberRole.admin, db)
    job = db.query(LegalAsyncJob).filter(
        LegalAsyncJob.id == job_id,
        LegalAsyncJob.organization_id == org_id,
    ).first()
    if not job:
        raise HTTPException(404, detail=err(JOB_NOT_FOUND))
    from app.services.jobs.async_job_service import cancel_job
    return cancel_job(
        db,
        job=job,
        actor_type="user",
        actor_id=str(current_user.id),
        reason_code="org_admin_cancel",
    )

