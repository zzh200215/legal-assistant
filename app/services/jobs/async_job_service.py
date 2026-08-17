"""异步 Job 统一契约服务（P1 API 统一化）：序列化 / 幂等取消 / 审计。

- serialize_job：JobOut 契约（job_id + task_id 兼容别名 + status_url）；
- cancel_job：幂等取消（queued→cancelled 直改；processing→置 cancel_requested 由
  消费者检查；已终态返回当前状态不报错）；取消行为写安全审计；
- 状态流转单一来源：全部读写 legal_async_jobs 一行，无第二套状态。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

_TERMINAL_STATUSES = ("succeeded", "failed", "cancelled", "expired")


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def serialize_job(job, *, open_api: bool = False) -> dict[str, Any]:
    """JobOut 序列化。task_id 为兼容别名（= job_id，旧客户端继续可用）。"""
    org_path = f"/api/developer/orgs/{job.organization_id}/async-jobs/{job.id}"
    status_url = f"/api/open/v1/tasks/{job.id}" if open_api else org_path
    error = None
    if job.status == "failed" and job.error_summary:
        error = {"code": "JOB_FAILED", "message": job.error_summary}
    return {
        "job_id": job.id,
        "task_id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "progress": job.progress,
        "status_url": status_url,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "ended_at": job.ended_at,
        "result_summary": job.result_summary,
        "error": error,
        "retry_count": job.retry_count,
        "estimated_completion": None,
    }


def cancel_job(
    db: Session,
    *,
    job,
    actor_type: str,
    actor_id: str,
    reason_code: str = "user_cancelled",
) -> dict[str, Any]:
    """幂等取消。返回 ``{"cancelled": bool, "job": <serialized>}``。

    - queued → cancelled（直接终态）
    - processing → cancel_requested=1（消费者检查后终止）
    - 已终态 → 返回当前状态（不报错，重复取消幂等）
    取消行为写安全审计（fail-closed：审计写失败抛异常，取消不生效）。
    """
    from app.services.org.security_audit_service import write_event

    if job.status in _TERMINAL_STATUSES:
        return {"cancelled": False, "job": serialize_job(job)}

    if job.status == "queued":
        job.status = "cancelled"
        job.ended_at = utc_now()
    elif job.status == "processing":
        job.cancel_requested = 1
    else:
        # 未知状态防御：不允许取消
        return {"cancelled": False, "job": serialize_job(job)}
    db.commit()
    db.refresh(job)

    write_event(
        event_type="job_cancel",
        actor_type=actor_type,
        actor_id=actor_id,
        result="success" if job.status == "cancelled" else "requested",
        organization_id=job.organization_id,
        target_type="legal_async_job",
        target_id=str(job.id),
        reason_code=reason_code,
        db=db,
    )
    return {"cancelled": job.status == "cancelled", "job": serialize_job(job)}
