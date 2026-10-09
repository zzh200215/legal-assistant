"""Durable workflow outbox delivery and lease recovery."""

import uuid
import json
from datetime import timedelta

import redis

from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.time import utc_now
from app.models.workflow import WorkflowEvent, WorkflowRun
from app.services.workflows.workflow_service import workflow_service


@celery_app.task(name="dispatch_workflow_outbox")
def dispatch_workflow_outbox_task():
    """Drain durable workflow events once; event rows remain replayable."""
    db = SessionLocal()
    owner = f"workflow-outbox:{uuid.uuid4().hex}"
    delivered = 0
    retried = 0
    failed = 0
    publisher = None
    try:
        publisher = redis.Redis.from_url(get_settings().REDIS_URL, decode_responses=True)
        for event in workflow_service.claim_outbox(db, owner=owner, limit=200):
            workflow_event = db.query(WorkflowEvent).filter(WorkflowEvent.id == event.workflow_event_id).first()
            run = db.query(WorkflowRun).filter(WorkflowRun.id == workflow_event.workflow_run_id).first() if workflow_event else None
            if not workflow_event or not run:
                workflow_service.release_outbox(
                    db, event_id=event.id, owner=owner,
                    error="workflow event or run is missing", retryable=False,
                )
                failed += 1
                continue
            try:
                message = {
                    "event_id": workflow_event.id,
                    "workflow_run_id": run.id,
                    "workflow_type": run.workflow_type,
                    "organization_id": run.organization_id,
                    "case_id": run.case_id,
                    "event_type": workflow_event.event_type,
                    "status": workflow_event.to_status,
                    "step": workflow_event.step,
                    "progress": workflow_event.progress,
                    "created_at": workflow_event.created_at.isoformat() if workflow_event.created_at else None,
                }
                publisher.publish("legal.workflow.events", json.dumps(message, separators=(",", ":")))
                workflow_service.mark_outbox_delivered(db, event_id=event.id, owner=owner)
                delivered += 1
            except Exception as exc:  # noqa: BLE001 - lease is released for retry
                released = workflow_service.release_outbox(
                    db, event_id=event.id, owner=owner, error=str(exc), retryable=True,
                )
                if released and released.status == "pending":
                    retried += 1
                else:
                    failed += 1
        return {"delivered": delivered, "retried": retried, "failed": failed}
    finally:
        if publisher is not None:
            publisher.close()
        db.close()


@celery_app.task(name="recover_stale_workflow_outbox")
def recover_stale_workflow_outbox_task():
    db = SessionLocal()
    cutoff = utc_now() - timedelta(minutes=10)
    try:
        from app.models.workflow import WorkflowOutboxEvent

        count = db.query(WorkflowOutboxEvent).filter(
            WorkflowOutboxEvent.status == "sending",
            WorkflowOutboxEvent.claimed_at < cutoff,
        ).update({
            WorkflowOutboxEvent.status: "pending",
            WorkflowOutboxEvent.claim_owner: None,
            WorkflowOutboxEvent.claimed_at: None,
        }, synchronize_session=False)
        db.commit()
        return {"recovered": count}
    finally:
        db.close()
