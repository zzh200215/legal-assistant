from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.models.document import DocumentParseJob
from app.core.time import utc_now


class DocumentJobService:
    def create_job(
        self,
        *,
        document_id: int,
        user_id: int,
        job_type: str,
        db: Session,
        task_id: str | None = None,
        status: str = "pending",
        progress: int | None = 0,
        current_step: str | None = None,
        message: str | None = None,
    ) -> DocumentParseJob:
        job = DocumentParseJob(
            document_id=document_id,
            user_id=user_id,
            job_type=job_type,
            task_id=task_id,
            status=status,
            progress=progress,
            current_step=current_step,
            message=message,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return job

    def get_job_by_task_id(self, task_id: str, db: Session) -> DocumentParseJob | None:
        return db.query(DocumentParseJob).filter(DocumentParseJob.task_id == task_id).first()

    def list_jobs(self, document_id: int, user_id: int, db: Session, limit: int = 20) -> list[DocumentParseJob]:
        return (
            db.query(DocumentParseJob)
            .filter(DocumentParseJob.document_id == document_id, DocumentParseJob.user_id == user_id)
            .order_by(DocumentParseJob.created_at.desc(), DocumentParseJob.id.desc())
            .limit(limit)
            .all()
        )

    def mark_started(
        self,
        task_id: str,
        db: Session,
        *,
        current_step: str | None = None,
        message: str | None = None,
        progress: int | None = None,
    ) -> DocumentParseJob | None:
        job = self.get_job_by_task_id(task_id, db)
        if not job:
            return None
        if not job.started_at:
            job.started_at = utc_now()
        job.status = "running"
        if current_step is not None:
            job.current_step = current_step
        if message is not None:
            job.message = message
        if progress is not None:
            job.progress = progress
        db.add(job)
        db.commit()
        db.refresh(job)
        return job

    def update_progress(
        self,
        task_id: str,
        db: Session,
        *,
        current_step: str | None = None,
        message: str | None = None,
        progress: int | None = None,
        retry_count: int | None = None,
    ) -> DocumentParseJob | None:
        job = self.get_job_by_task_id(task_id, db)
        if not job:
            return None
        if current_step is not None:
            job.current_step = current_step
        if message is not None:
            job.message = message
        if progress is not None:
            job.progress = progress
        if retry_count is not None:
            job.retry_count = retry_count
        if not job.started_at:
            job.started_at = utc_now()
        if job.status == "pending":
            job.status = "running"
        db.add(job)
        db.commit()
        db.refresh(job)
        return job

    def mark_succeeded(
        self,
        task_id: str,
        db: Session,
        *,
        message: str | None = None,
        result_summary: str | None = None,
    ) -> DocumentParseJob | None:
        job = self.get_job_by_task_id(task_id, db)
        if not job:
            return None
        job.status = "succeeded"
        job.progress = 100
        job.finished_at = utc_now()
        if message is not None:
            job.message = message
        if result_summary is not None:
            job.result_summary = result_summary
        db.add(job)
        db.commit()
        db.refresh(job)
        return job

    def mark_failed(
        self,
        task_id: str,
        db: Session,
        *,
        error_message: str,
        message: str | None = None,
        retry_count: int | None = None,
    ) -> DocumentParseJob | None:
        job = self.get_job_by_task_id(task_id, db)
        if not job:
            return None
        job.status = "failed"
        job.finished_at = utc_now()
        job.error_message = error_message
        if message is not None:
            job.message = message
        if retry_count is not None:
            job.retry_count = retry_count
        db.add(job)
        db.commit()
        db.refresh(job)
        return job

    def attach_task_id(self, job_id: int, task_id: str, db: Session) -> DocumentParseJob | None:
        job = db.query(DocumentParseJob).filter(DocumentParseJob.id == job_id).first()
        if not job:
            return None
        job.task_id = task_id
        db.add(job)
        db.commit()
        db.refresh(job)
        return job


document_job_service = DocumentJobService()
