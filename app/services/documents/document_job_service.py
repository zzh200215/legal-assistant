from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import or_
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

    def find_or_create_job(
        self,
        *,
        document_id: int,
        user_id: int,
        job_type: str,
        db: Session,
        task_id: str | None = None,
    ) -> DocumentParseJob:
        if task_id:
            existing = self.get_job_by_task_id(task_id, db)
            if existing:
                return existing
        return self.create_job(
            document_id=document_id,
            user_id=user_id,
            job_type=job_type,
            db=db,
            task_id=task_id,
            current_step="submitted",
            message="文档处理任务已提交",
        )

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

    def list_stale_jobs(
        self,
        db: Session,
        *,
        stale_before: datetime,
        statuses: tuple[str, ...] = ("running", "pending"),
        limit: int = 50,
    ) -> list[DocumentParseJob]:
        """返回租约过期（或从未领取）的进行中任务，供回收调度。"""
        return (
            db.query(DocumentParseJob)
            .filter(
                DocumentParseJob.status.in_(statuses),
                (
                    (DocumentParseJob.lease_expires_at.is_(None))
                    | (DocumentParseJob.lease_expires_at < stale_before)
                ),
            )
            .order_by(DocumentParseJob.created_at.asc())
            .limit(limit)
            .all()
        )

    # ── 租约（lease）：worker 领取 / 心跳续约 / 释放 / 回收 ─────────────────────
    def claim_job(self, job_id: int, owner: str, ttl_seconds: int, db: Session) -> bool:
        """条件领取：仅当 lease 未被其他 worker 持有（过期或空）时成功。

        用原子条件 UPDATE 认领，避免读-改-写 TOCTOU 导致两个 worker 同时领取同一任务。
        """
        now = utc_now()
        expires_at = now + timedelta(seconds=ttl_seconds)
        rowcount = (
            db.query(DocumentParseJob)
            .filter(
                DocumentParseJob.id == job_id,
                or_(
                    DocumentParseJob.lease_owner.is_(None),
                    DocumentParseJob.lease_expires_at.is_(None),
                    DocumentParseJob.lease_expires_at < now,
                    DocumentParseJob.lease_owner == owner,
                ),
            )
            .update(
                {
                    DocumentParseJob.lease_owner: owner,
                    DocumentParseJob.lease_expires_at: expires_at,
                },
                synchronize_session=False,
            )
        )
        if rowcount != 1:
            db.rollback()
            return False
        # 租约已认领成功，再更新非租约字段（started_at/status）。
        job = db.query(DocumentParseJob).filter(DocumentParseJob.id == job_id).first()
        if not job:
            db.rollback()
            return False
        if not job.started_at:
            job.started_at = now
        if job.status in ("pending",):
            job.status = "running"
        db.commit()
        return True

    def renew_lease(self, job_id: int, owner: str, ttl_seconds: int, db: Session) -> bool:
        """心跳：仅 owner 可续约，防止其他 worker 误刷新。"""
        job = db.query(DocumentParseJob).filter(DocumentParseJob.id == job_id).first()
        if not job or job.lease_owner != owner:
            return False
        job.lease_expires_at = utc_now() + timedelta(seconds=ttl_seconds)
        db.commit()
        return True

    def release_lease(self, job_id: int, owner: str, db: Session) -> None:
        job = db.query(DocumentParseJob).filter(DocumentParseJob.id == job_id).first()
        if job and job.lease_owner == owner:
            job.lease_owner = None
            job.lease_expires_at = None
            db.commit()

    def reset_expired_lease(self, job: DocumentParseJob, db: Session) -> None:
        """回收：清空租约并复位状态，供重新领取。"""
        job.lease_owner = None
        job.lease_expires_at = None
        job.status = "pending"
        db.commit()

    def plan_recovery(
        self,
        db: Session,
        *,
        stale_before: datetime,
        limit: int = 50,
    ) -> list[tuple[DocumentParseJob, str]]:
        """租约过期任务回收规划：返回 [(job, next_task_name)]。

        next_task_name ∈ {"parse", "chunk", "index"}，按 job_type 决定重投哪个阶段任务。
        文档已删除的任务仅复位不重投。供 beat 回收任务使用，独立可测。
        """
        from app.models.document import Document

        stale = self.list_stale_jobs(db, stale_before=stale_before, limit=limit)
        plans: list[tuple[DocumentParseJob, str]] = []
        for job in stale:
            doc = db.query(Document).filter(Document.id == job.document_id).first()
            if not doc:
                self.reset_expired_lease(job, db)
                continue
            self.reset_expired_lease(job, db)
            plans.append((job, self._recovery_task_for_job_type(job.job_type)))
        return plans

    @staticmethod
    def _recovery_task_for_job_type(job_type: str | None) -> str:
        if job_type == "document_chunk":
            return "chunk"
        if job_type == "document_index":
            return "index"
        return "parse"

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
