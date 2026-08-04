from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.time import utc_now
from app.models.document import DocumentQARecord

settings = get_settings()


def _dumps(payload) -> str | None:
    if payload is None:
        return None
    return json.dumps(payload, ensure_ascii=False)


def _loads(payload):
    if not payload:
        return []
    try:
        data = json.loads(payload)
    except (TypeError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


class DocumentQAService:
    FEEDBACK_POSITIVE = "positive"
    FEEDBACK_NEGATIVE = "negative"
    FEEDBACK_OPEN = "open"
    FEEDBACK_RESOLVED = "resolved"

    def record(
        self,
        *,
        document_id: int,
        user_id: int,
        question: str,
        answer: str,
        db: Session,
        citations: list[dict] | None = None,
        hit_chunks: list[dict] | None = None,
        latency_ms: int | None = None,
        session_id: int | None = None,
        source: str = "document",
    ) -> DocumentQARecord:
        record = DocumentQARecord(
            document_id=document_id,
            user_id=user_id,
            session_id=session_id,
            question=question,
            answer=answer,
            citations=_dumps(citations),
            hit_chunks=_dumps(hit_chunks),
            model_name=settings.LLM_MODEL,
            latency_ms=latency_ms,
            source=source,
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return record

    def get_record(self, qa_record_id: int, db: Session) -> DocumentQARecord | None:
        return db.query(DocumentQARecord).filter(DocumentQARecord.id == qa_record_id).first()

    def submit_feedback(
        self,
        *,
        qa_record_id: int,
        user_id: int,
        feedback_value: str,
        db: Session,
        feedback_reason: str | None = None,
        feedback_note: str | None = None,
    ) -> DocumentQARecord:
        record = (
            db.query(DocumentQARecord)
            .filter(DocumentQARecord.id == qa_record_id, DocumentQARecord.user_id == user_id)
            .first()
        )
        if not record:
            raise ValueError("QA record not found")

        normalized_value = (feedback_value or "").strip().lower()
        if normalized_value not in {self.FEEDBACK_POSITIVE, self.FEEDBACK_NEGATIVE}:
            raise ValueError("Unsupported feedback value")

        normalized_reason = (feedback_reason or "").strip() or None
        normalized_note = (feedback_note or "").strip() or None
        now = utc_now()

        record.feedback_value = normalized_value
        record.feedback_reason = normalized_reason if normalized_value == self.FEEDBACK_NEGATIVE else None
        record.feedback_note = normalized_note
        record.feedback_created_at = now
        record.feedback_resolution_note = None
        record.feedback_resolved_by = None
        if normalized_value == self.FEEDBACK_POSITIVE:
            record.feedback_status = self.FEEDBACK_RESOLVED
            record.feedback_resolved_at = now
        else:
            record.feedback_status = self.FEEDBACK_OPEN
            record.feedback_resolved_at = None

        db.add(record)
        db.commit()
        db.refresh(record)
        return record

    def resolve_feedback(
        self,
        *,
        qa_record_id: int,
        resolver_id: int,
        db: Session,
        resolution_note: str | None = None,
    ) -> DocumentQARecord:
        record = self.get_record(qa_record_id, db)
        if not record:
            raise ValueError("QA record not found")
        if not record.feedback_value:
            raise ValueError("Feedback not submitted")

        record.feedback_status = self.FEEDBACK_RESOLVED
        record.feedback_resolved_at = utc_now()
        record.feedback_resolved_by = resolver_id
        record.feedback_resolution_note = (resolution_note or "").strip() or None
        db.add(record)
        db.commit()
        db.refresh(record)
        return record

    def list_records(self, document_id: int, user_id: int, db: Session, limit: int = 20) -> list[DocumentQARecord]:
        return (
            db.query(DocumentQARecord)
            .filter(DocumentQARecord.document_id == document_id, DocumentQARecord.user_id == user_id)
            .order_by(DocumentQARecord.created_at.desc(), DocumentQARecord.id.desc())
            .limit(limit)
            .all()
        )

    def serialize_record(self, record: DocumentQARecord) -> dict:
        return {
            "id": record.id,
            "document_id": record.document_id,
            "document_title": record.document.title if record.document else None,
            "user_id": record.user_id,
            "session_id": record.session_id,
            "question": record.question,
            "answer": record.answer,
            "citations": _loads(record.citations),
            "hit_chunks": _loads(record.hit_chunks),
            "model_name": record.model_name,
            "latency_ms": record.latency_ms,
            "source": record.source,
            "feedback_value": record.feedback_value,
            "feedback_reason": record.feedback_reason,
            "feedback_note": record.feedback_note,
            "feedback_status": record.feedback_status,
            "feedback_created_at": record.feedback_created_at,
            "feedback_resolved_at": record.feedback_resolved_at,
            "feedback_resolution_note": record.feedback_resolution_note,
            "feedback_resolved_by": record.feedback_resolved_by,
            "created_at": record.created_at,
        }


document_qa_service = DocumentQAService()
