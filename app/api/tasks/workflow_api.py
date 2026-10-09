"""Product-facing workflow run status endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, verify_case_access
from app.core.database import get_db
from app.models.user import User
from app.models.document import Document
from app.models.legal_platform import LegalAsyncJob
from app.models.workflow import WorkflowEvent
from app.services.workflows.workflow_service import workflow_service

router = APIRouter()


def _serialize(row):
    return {
        "id": row.id, "task_id": row.task_id, "workflow_type": row.workflow_type,
        "organization_id": row.organization_id, "user_id": row.user_id, "case_id": row.case_id,
        "business_key": row.business_key, "status": row.status, "current_step": row.current_step,
        "progress": row.progress, "attempt": row.attempt, "error_code": row.error_code,
        "error_message": row.error_message, "checkpoint_json": row.checkpoint_json,
        "cancel_requested_at": row.cancel_requested_at, "cancel_reason": row.cancel_reason,
        "cancel_requested_by": row.cancel_requested_by,
        "trace_id": row.trace_id, "request_id": row.request_id,
        "queued_at": row.queued_at, "started_at": row.started_at,
        "finished_at": row.finished_at, "created_at": row.created_at, "updated_at": row.updated_at,
    }


def _serialize_event(row):
    return {
        "id": row.id, "workflow_run_id": row.workflow_run_id, "event_type": row.event_type,
        "from_status": row.from_status, "to_status": row.to_status, "step": row.step,
        "progress": row.progress, "payload_json": row.payload_json,
        "actor_type": row.actor_type, "actor_id": row.actor_id, "created_at": row.created_at,
    }


@router.get("/workflows")
def list_workflows(
    case_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.organization_id is None:
        return []
    if case_id is not None:
        verify_case_access(case_id, current_user.id, db)
    rows = workflow_service.list_for_scope(
        db, organization_id=current_user.organization_id, case_id=case_id, status=status, limit=limit,
    )
    return [_serialize(row) for row in rows]


@router.get("/workflows/{workflow_id}")
def get_workflow(
    workflow_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.organization_id is None:
        raise HTTPException(status_code=404, detail="WORKFLOW_NOT_FOUND")
    row = workflow_service.get(db, workflow_id=workflow_id, organization_id=current_user.organization_id)
    if row is None:
        raise HTTPException(status_code=404, detail="WORKFLOW_NOT_FOUND")
    if row.case_id is not None:
        verify_case_access(row.case_id, current_user.id, db)
    return {"workflow": _serialize(row), "events": [_serialize_event(event) for event in workflow_service.events(
        db, workflow_id=row.id, organization_id=current_user.organization_id,
    )]}


@router.post("/workflows/{workflow_id}/cancel")
def cancel_workflow(
    workflow_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.organization_id is None:
        raise HTTPException(status_code=404, detail="WORKFLOW_NOT_FOUND")
    row = workflow_service.get(db, workflow_id=workflow_id, organization_id=current_user.organization_id)
    if row is None:
        raise HTTPException(status_code=404, detail="WORKFLOW_NOT_FOUND")
    if row.case_id is not None:
        verify_case_access(row.case_id, current_user.id, db)
    if row.status in {"succeeded", "failed"}:
        raise HTTPException(status_code=409, detail="WORKFLOW_NOT_CANCELLABLE")
    if row.status == "cancelled":
        # Repeated cancellation is idempotent. The database row is already in
        # its terminal state, so do not append another event or revoke again.
        return _serialize(row)
    row = workflow_service.request_cancel(
        db, workflow_id=row.id, organization_id=current_user.organization_id,
        actor_id=current_user.id, reason="user_cancelled",
    )
    if row and row.task_id:
        try:
            from app.core.celery_app import celery_app
            celery_app.control.revoke(row.task_id, terminate=False)
        except Exception:  # noqa: BLE001 - DB cancellation remains authoritative
            pass
    return _serialize(row)


@router.post("/workflows/{workflow_id}/retry")
def retry_workflow(
    workflow_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Requeue a failed business task while retaining the failed run history."""
    if current_user.organization_id is None:
        raise HTTPException(status_code=404, detail="WORKFLOW_NOT_FOUND")
    row = workflow_service.get(db, workflow_id=workflow_id, organization_id=current_user.organization_id)
    if row is None:
        raise HTTPException(status_code=404, detail="WORKFLOW_NOT_FOUND")
    if row.case_id is not None:
        verify_case_access(row.case_id, current_user.id, db)
    if row.status != "failed":
        raise HTTPException(status_code=409, detail="WORKFLOW_NOT_RETRYABLE")
    retry_requested = db.query(WorkflowEvent).filter(
        WorkflowEvent.workflow_run_id == row.id,
        WorkflowEvent.event_type == "workflow.retry_requested",
    ).order_by(WorkflowEvent.id.desc()).first()
    if retry_requested is not None:
        raise HTTPException(status_code=409, detail="WORKFLOW_RETRY_ALREADY_REQUESTED")
    active = db.query(type(row)).filter(
        type(row).organization_id == row.organization_id,
        type(row).workflow_type == row.workflow_type,
        type(row).business_key == row.business_key,
        type(row).status.in_(("queued", "running", "cancelling", "retrying")),
    ).first()
    if active is not None:
        raise HTTPException(status_code=409, detail="WORKFLOW_RETRY_ALREADY_ACTIVE")

    from app.core.obs_context import enqueue_headers as obs_enqueue_headers

    task = None
    business_key = row.business_key or ""
    try:
        if row.workflow_type in {"parse_document", "document_chunk", "document_index", "summarize_document", "analyze_document"}:
            document_id = int(business_key.rsplit(":", 1)[-1])
            document = db.query(Document).filter(
                Document.id == document_id,
                Document.organization_id == current_user.organization_id,
            ).first()
            if document is None:
                raise ValueError("Document not found")
            from app.tasks import (
                analyze_document_task, document_chunk_task, document_index_task,
                parse_document_task, summarize_document_task,
            )
            headers = obs_enqueue_headers()
            if row.workflow_type == "parse_document":
                task = parse_document_task.delay(document.id, document.version_number, document.file_type, headers=headers)
            elif row.workflow_type == "document_chunk":
                task = document_chunk_task.delay(document.id, document.version_number, headers=headers)
            elif row.workflow_type == "document_index":
                task = document_index_task.delay(document.id, document.version_number, headers=headers)
            elif row.workflow_type == "summarize_document":
                task = summarize_document_task.delay(document.id, row.user_id or current_user.id, headers=headers)
            else:
                task = analyze_document_task.delay(document.id, row.user_id or current_user.id, headers=headers)
        elif row.workflow_type == "process_open_contract_review":
            job_id = int(business_key.rsplit(":", 1)[-1])
            job = db.query(LegalAsyncJob).filter(
                LegalAsyncJob.id == job_id,
                LegalAsyncJob.organization_id == current_user.organization_id,
            ).first()
            if job is None:
                raise ValueError("Contract review job not found")
            from app.tasks.legal_tasks import process_open_contract_review_task
            task = process_open_contract_review_task.delay(job.id, headers=obs_enqueue_headers())
        else:
            raise ValueError("Task type is not retryable")
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="WORKFLOW_NOT_RETRYABLE") from exc

    workflow_service.record_retry_requested(
        db, workflow_id=row.id, task_id=task.id, actor_id=current_user.id,
    )
    return {"retry_of": row.id, "task_id": task.id, "status": "queued"}
