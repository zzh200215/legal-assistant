"""P1 审计导出服务：分页/流式生成导出文件 + 清单 + 导出事件。

- 导出按 seq_no keyset 分页流式写 JSONL（含哈希字段，消费方可独立校验），
  绝不一次性载入全部审计记录。
- 导出前强制哈希链完整性校验；链损坏 → 导出冻结（fail-closed）。
- 清单记录查询条件、操作者、生成时间、记录数、文件 sha256 与格式版本。
- 导出行为本身写入安全审计（success/failure/blocked）；下载同样可审计。
- 输出文件只含脱敏后的审计字段，不落业务正文。
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.legal_notifications import SecurityAuditEvent
from app.models.legal_platform import LegalAsyncJob
from app.services.org.security_audit_service import verify_chain, write_event

logger = logging.getLogger(__name__)

EXPORT_FORMAT = "audit-export-v1"
_BATCH = 500

_EXPORT_FIELDS = (
    "seq_no", "audit_id", "schema_version", "event_type", "action", "actor_type",
    "actor_id", "organization_id", "target_type", "target_id", "result", "decision",
    "reason_code", "resource_version", "request_id", "trace_id", "task_id",
    "agent_run_id", "detail_json_hash", "sanitized_metadata", "occurred_at",
    "prev_hash", "current_hash",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _export_dir() -> Path:
    return Path(get_settings().OBS_AUDIT_ARCHIVE_DIR).parent / "exports" / "audit"


def _serialize_event(event: SecurityAuditEvent) -> dict:
    return {
        "seq_no": event.seq_no,
        "audit_id": event.audit_id or str(event.seq_no),
        "schema_version": int(event.schema_version or 1),
        "event_type": event.event_type,
        "action": event.action,
        "actor_type": event.actor_type,
        "actor_id": event.actor_id,
        "organization_id": event.organization_id,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "result": event.result,
        "decision": event.decision,
        "reason_code": event.reason_code,
        "resource_version": event.resource_version,
        "request_id": event.request_id,
        "trace_id": event.trace_id,
        "task_id": event.task_id,
        "agent_run_id": event.agent_run_id,
        "detail_json_hash": event.detail_json_hash,
        "sanitized_metadata": event.sanitized_metadata,
        "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
        "prev_hash": event.prev_hash,
        "current_hash": event.current_hash,
    }


def run_export_job(db: Session, job_id: int) -> dict:
    """执行审计导出任务（worker 调用）。返回状态摘要；失败路径已记账。"""
    job = (
        db.query(LegalAsyncJob)
        .filter(LegalAsyncJob.id == job_id, LegalAsyncJob.job_type == "audit_export")
        .first()
    )
    if job is None:
        return {"status": "not_found", "job_id": job_id}
    if job.status not in ("queued", "processing"):
        return {"status": "skipped", "job_id": job_id, "job_status": job.status}
    if job.cancel_requested:
        # 取消优先：已请求取消的任务不再执行（幂等取消语义）。
        job.status = "cancelled"
        job.ended_at = _utcnow()
        job.error_summary = "任务已被取消"
        db.commit()
        return {"status": "cancelled", "job_id": job_id}

    job.status = "processing"
    job.started_at = _utcnow()
    db.commit()

    try:
        params = json.loads(job.input_json or "{}")
    except (TypeError, ValueError):
        params = {}
    organization_id = int(params.get("organization_id") or job.organization_id)
    event_type = params.get("event_type")
    start = params.get("start")
    end = params.get("end")

    # 导出前完整性校验：链损坏即冻结（fail-closed）。
    integrity = verify_chain(organization_id=organization_id)
    if not integrity["intact"]:
        _finish_failed(db, job, "audit_chain_broken",
                       f"断链 {len(integrity['broken'])} 处；导出已冻结")
        _write_audit_event(organization_id, job, "blocked", "integrity_failure")
        return {"status": "failed", "job_id": job_id, "reason": "audit_chain_broken"}

    export_dir = _export_dir()
    export_dir.mkdir(parents=True, exist_ok=True)
    data_path = export_dir / f"audit_export_{job_id}.jsonl"
    manifest_path = export_dir / f"audit_export_{job_id}.manifest.json"

    hasher = hashlib.sha256()
    count = 0
    last_seq = 0
    with data_path.open("w", encoding="utf-8") as fh:
        while True:
            q = (
                db.query(SecurityAuditEvent)
                .filter(
                    SecurityAuditEvent.organization_id == organization_id,
                    SecurityAuditEvent.seq_no > last_seq,
                )
                .order_by(SecurityAuditEvent.seq_no)
                .limit(_BATCH)
            )
            if event_type:
                q = q.filter(SecurityAuditEvent.event_type == event_type)
            if start:
                q = q.filter(SecurityAuditEvent.occurred_at >= start)
            if end:
                q = q.filter(SecurityAuditEvent.occurred_at <= end)
            batch = q.all()
            if not batch:
                break
            for event in batch:
                # 每批检查取消请求：中断并清理部分产物（幂等取消语义）。
                if job.cancel_requested:
                    fh.close()
                    data_path.unlink(missing_ok=True)
                    job.status = "cancelled"
                    job.ended_at = _utcnow()
                    job.error_summary = "任务已被取消"
                    db.commit()
                    return {"status": "cancelled", "job_id": job_id, "count": count}
                line = json.dumps(_serialize_event(event), ensure_ascii=False)
                fh.write(line + "\n")
                hasher.update((line + "\n").encode("utf-8"))
                count += 1
                last_seq = event.seq_no
            db.expire_all()

    file_hash = hasher.hexdigest()
    generated_at = _utcnow().isoformat()
    manifest = {
        "format": EXPORT_FORMAT,
        "job_id": job_id,
        "organization_id": organization_id,
        "operator_id": job.created_by,
        "conditions": {"event_type": event_type, "start": start, "end": end},
        "generated_at": generated_at,
        "record_count": count,
        "file": data_path.name,
        "sha256": file_hash,
        "verification": "sha256(file)；每条记录含 prev_hash/current_hash 可独立校验",
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    # 导出事件入审计（export 类写失败为 block → fail-closed，任务失败可重试）。
    try:
        _write_audit_event(organization_id, job, "success", f"job:{job_id}:{count}")
    except Exception as exc:  # noqa: BLE001 - 审计事件写失败按 fail-closed 处理
        _finish_failed(db, job, "audit_event_write_failed", str(exc)[:500])
        raise

    job.status = "succeeded"
    job.ended_at = _utcnow()
    job.result_summary = f"count={count}; sha256={file_hash[:16]}"
    job.output_json = json.dumps({
        "file": str(data_path),
        "manifest": str(manifest_path),
        "count": count,
        "sha256": file_hash,
        "generated_at": generated_at,
    }, ensure_ascii=False)
    db.commit()
    return {"status": "succeeded", "job_id": job_id, "count": count, "sha256": file_hash}


def _finish_failed(db: Session, job: LegalAsyncJob, code: str, message: str) -> None:
    job.status = "failed"
    job.ended_at = _utcnow()
    job.error_summary = f"{code}: {message[:500]}"
    db.commit()


def _write_audit_event(organization_id: int, job: LegalAsyncJob, result: str, target_id: str) -> None:
    """导出事件写审计；export 类失败策略为 block（抛 AuditWriteError，fail-closed）。"""
    write_event(
        event_type="export",
        actor_type="user",
        actor_id=str(job.created_by),
        result=result,
        organization_id=organization_id,
        target_type="security_audit_events",
        target_id=target_id,
        action="audit_export",
        reason_code="audit_export_job" if result == "success" else "audit_export_frozen",
    )


audit_export_service = type("_Svc", (), {
    "run_export_job": staticmethod(run_export_job),
    "EXPORT_FORMAT": EXPORT_FORMAT,
})()
