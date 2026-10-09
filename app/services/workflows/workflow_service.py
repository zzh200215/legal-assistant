"""Durable workflow state and transactional outbox facade."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.observability_sanitizer import redact_payload, truncate_text
from app.models.workflow import WorkflowEvent, WorkflowOutboxEvent, WorkflowRun


TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
WORKFLOW_OUTBOX_MAX_ATTEMPTS = 5


class WorkflowCancelled(RuntimeError):
    """Raised inside a worker when a cooperative cancellation is observed."""

    error_code = "WORKFLOW_CANCELLED"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _payload(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    safe_value = redact_payload(value)
    return json.dumps(safe_value, ensure_ascii=False, separators=(",", ":"))[:4000]


class WorkflowService:
    def record_retry_requested(
        self, db: Session, *, workflow_id: int, task_id: str,
        actor_id: int | None = None,
    ) -> WorkflowEvent | None:
        """Append an auditable retry request without mutating the failed run.

        A retry receives a new Celery id and therefore a new ``WorkflowRun``
        when the worker starts. Keeping the original row terminal preserves
        the complete failure history and avoids reusing the unique task id.
        """
        row = self.get(db, workflow_id=workflow_id)
        if row is None:
            return None
        event = self._append_event(
            db, row, event_type="workflow.retry_requested", from_status=row.status,
            to_status="queued", step="retry_requested", progress=row.progress,
            payload={"retry_task_id": task_id, "actor_id": actor_id},
        )
        db.commit()
        db.refresh(event)
        return event

    def start(
        self, db: Session, *, workflow_type: str, task_id: str | None = None,
        organization_id: int | None = None, user_id: int | None = None,
        case_id: int | None = None, business_key: str | None = None,
        idempotency_key: str | None = None, trace_id: str | None = None,
        request_id: str | None = None, payload: dict[str, Any] | None = None,
    ) -> WorkflowRun:
        row = db.query(WorkflowRun).filter(WorkflowRun.task_id == task_id).first() if task_id else None
        if row is None:
            row = WorkflowRun(
                task_id=task_id, workflow_type=workflow_type, organization_id=organization_id,
                user_id=user_id, case_id=case_id, business_key=business_key,
                idempotency_key=idempotency_key, status="running", current_step="started",
                progress=0, attempt=1, trace_id=trace_id, request_id=request_id,
                started_at=_now(),
            )
            db.add(row)
            db.flush()
            from_status = None
        else:
            # A task can reach ``task_prerun`` after its queued cancellation was
            # committed. Preserve that authoritative request so the worker's
            # first cooperative check terminates it instead of reviving it.
            if row.cancel_requested_at is not None or row.status == "cancelled":
                db.refresh(row)
                return row
            from_status = row.status
            row.status = "running"
            row.current_step = "started"
            row.progress = 0
            row.attempt = max(1, row.attempt + 1)
            row.started_at = _now()
            row.finished_at = None
            row.error_code = None
            row.error_message = None
            row.cancel_requested_at = None
            row.cancel_reason = None
            row.cancel_requested_by = None
        self._append_event(
            db, row, event_type="workflow.started", from_status=from_status,
            to_status="running", step="started", progress=0, payload=payload,
        )
        db.commit()
        db.refresh(row)
        return row

    def transition(
        self, db: Session, *, workflow_id: int | None = None, task_id: str | None = None,
        status: str, step: str | None = None, progress: int | None = None,
        error_code: str | None = None, error_message: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> WorkflowRun | None:
        row = self._find(db, workflow_id=workflow_id, task_id=task_id)
        if row is None:
            return None
        previous = row.status
        row.status = status
        row.current_step = step or row.current_step
        if progress is not None:
            row.progress = max(0, min(100, progress))
        if error_code:
            row.error_code = error_code[:64]
        if error_message is not None:
            row.error_message = truncate_text(error_message, 1000)
        if row.cancel_requested_at is not None and status != "cancelled":
            status = "cancelled"
            row.status = status
            row.current_step = "cancelled"
            row.error_code = WorkflowCancelled.error_code
            row.error_message = "任务已被取消"
            row.finished_at = _now()
        elif status in TERMINAL_STATUSES:
            row.finished_at = _now()
            if status == "succeeded":
                row.progress = 100
        self._append_event(
            db, row, event_type=f"workflow.{status}", from_status=previous,
            to_status=status, step=step, progress=row.progress, payload=payload,
        )
        db.commit()
        db.refresh(row)
        return row

    def request_cancel(
        self, db: Session, *, workflow_id: int, organization_id: int | None = None,
        actor_id: int | None = None, reason: str = "user_cancelled",
    ) -> WorkflowRun | None:
        row = self.get(db, workflow_id=workflow_id, organization_id=organization_id)
        if row is None or row.status in TERMINAL_STATUSES:
            return row
        if row.cancel_requested_at is not None and row.status == "cancelling":
            return row
        previous = row.status
        row.cancel_requested_at = row.cancel_requested_at or _now()
        row.cancel_reason = reason[:256]
        row.cancel_requested_by = actor_id
        if row.status == "queued":
            row.status = "cancelled"
            row.current_step = "cancelled"
            row.finished_at = _now()
            to_status = "cancelled"
        else:
            row.status = "cancelling"
            row.current_step = "cancellation_requested"
            to_status = "cancelling"
        self._append_event(
            db, row, event_type="workflow.cancel_requested", from_status=previous,
            to_status=to_status, step="cancellation_requested", progress=row.progress,
            payload={"reason": reason[:256], "actor_id": actor_id},
        )
        db.commit()
        db.refresh(row)
        return row

    def ensure_not_cancelled(self, db: Session, *, task_id: str) -> WorkflowRun | None:
        row = self._find(db, task_id=task_id)
        if row is None:
            return None
        db.refresh(row)
        if row.cancel_requested_at is not None or row.status in {"cancelling", "cancelled"}:
            if row.status != "cancelled":
                self.transition(
                    db, workflow_id=row.id, status="cancelled", step="cancelled",
                    error_code=WorkflowCancelled.error_code, error_message="任务已被取消",
                )
            raise WorkflowCancelled("Workflow cancellation requested")
        return row

    def update_checkpoint(self, db: Session, *, task_id: str, checkpoint: dict[str, Any]) -> WorkflowRun | None:
        row = self._find(db, task_id=task_id)
        if row is None:
            return None
        row.checkpoint_json = _payload(checkpoint)
        db.commit()
        return row

    def get(self, db: Session, *, workflow_id: int, organization_id: int | None = None) -> WorkflowRun | None:
        query = db.query(WorkflowRun).filter(WorkflowRun.id == workflow_id)
        if organization_id is not None:
            query = query.filter(WorkflowRun.organization_id == organization_id)
        return query.first()

    def events(self, db: Session, *, workflow_id: int, organization_id: int | None = None) -> list[WorkflowEvent]:
        row = self.get(db, workflow_id=workflow_id, organization_id=organization_id)
        if row is None:
            return []
        return db.query(WorkflowEvent).filter(
            WorkflowEvent.workflow_run_id == workflow_id,
        ).order_by(WorkflowEvent.created_at.asc(), WorkflowEvent.id.asc()).all()

    def list_for_scope(
        self, db: Session, *, organization_id: int, case_id: int | None = None,
        status: str | None = None, limit: int = 50,
    ) -> list[WorkflowRun]:
        query = db.query(WorkflowRun).filter(WorkflowRun.organization_id == organization_id)
        if case_id is not None:
            query = query.filter(WorkflowRun.case_id == case_id)
        if status:
            query = query.filter(WorkflowRun.status == status)
        return query.order_by(WorkflowRun.created_at.desc(), WorkflowRun.id.desc()).limit(max(1, min(limit, 200))).all()

    def claim_outbox(self, db: Session, *, owner: str, limit: int = 100) -> list[WorkflowOutboxEvent]:
        now = _now()
        rows = db.query(WorkflowOutboxEvent).filter(
            WorkflowOutboxEvent.status == "pending",
            WorkflowOutboxEvent.available_at <= now,
        ).order_by(WorkflowOutboxEvent.id.asc()).with_for_update(skip_locked=True).limit(
            max(1, min(limit, 500))
        ).all()
        for row in rows:
            row.status = "sending"
            row.attempts += 1
            row.claim_owner = owner
            row.claimed_at = now
        db.commit()
        return rows

    def mark_outbox_delivered(self, db: Session, *, event_id: int, owner: str) -> None:
        row = db.query(WorkflowOutboxEvent).filter(
            WorkflowOutboxEvent.id == event_id,
            WorkflowOutboxEvent.claim_owner == owner,
            WorkflowOutboxEvent.status == "sending",
        ).first()
        if row is None:
            return
        row.status = "delivered"
        row.delivered_at = _now()
        db.commit()

    def release_outbox(
        self, db: Session, *, event_id: int, owner: str, error: str,
        retryable: bool = True,
    ) -> WorkflowOutboxEvent | None:
        """Release a claimed event after delivery failure.

        Transient publisher failures go back to ``pending`` with bounded
        exponential backoff. Poisoned or exhausted events move to ``failed``
        so a single malformed row cannot block the outbox forever.
        """
        row = db.query(WorkflowOutboxEvent).filter(
            WorkflowOutboxEvent.id == event_id,
            WorkflowOutboxEvent.claim_owner == owner,
            WorkflowOutboxEvent.status == "sending",
        ).first()
        if row is None:
            return None
        row.last_error = truncate_text(error, 512)
        should_retry = retryable and row.attempts < WORKFLOW_OUTBOX_MAX_ATTEMPTS
        if should_retry:
            row.status = "pending"
            delay = min(300, 2 ** max(0, row.attempts - 1))
            row.available_at = _now() + timedelta(seconds=delay)
        else:
            row.status = "failed"
        row.claim_owner = None
        row.claimed_at = None
        db.commit()
        db.refresh(row)
        return row

    @staticmethod
    def _find(db: Session, *, workflow_id: int | None = None, task_id: str | None = None) -> WorkflowRun | None:
        if workflow_id is not None:
            return db.query(WorkflowRun).filter(WorkflowRun.id == workflow_id).first()
        if task_id:
            return db.query(WorkflowRun).filter(WorkflowRun.task_id == task_id).first()
        return None

    @staticmethod
    def _append_event(
        db: Session, row: WorkflowRun, *, event_type: str, from_status: str | None,
        to_status: str | None, step: str | None, progress: int | None,
        payload: dict[str, Any] | None,
    ) -> WorkflowEvent:
        event = WorkflowEvent(
            workflow_run_id=row.id, event_type=event_type, from_status=from_status,
            to_status=to_status, step=step, progress=progress, payload_json=_payload(payload),
        )
        db.add(event)
        db.flush()
        db.add(WorkflowOutboxEvent(workflow_event_id=event.id, event_type=event_type))
        return event


workflow_service = WorkflowService()
