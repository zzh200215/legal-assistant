from app.core.database import SessionLocal
from app.core.observability import log_async_task_event
from app.core.observability_sanitizer import sanitize_background_error_message
from app.models.connector import ConnectorSyncJob, ExternalConnector
from app.services.document_job_service import document_job_service
from app.tasks.runtime import background_error_detail
def retry_task(
    self,
    exc: Exception,
    *,
    user_id: int | None,
    target_type: str,
    target_id: int | None,
    action_prefix: str,
    log_event=log_async_task_event,
    session_factory=SessionLocal,
    document_jobs=document_job_service,
):
    retries = int(getattr(self.request, "retries", 0) or 0)
    max_retries = 2
    if target_type == "document":
        db = session_factory()
        try:
            document_jobs.update_progress(
                self.request.id,
                db,
                current_step="retrying",
                message=f"任务重试中，第 {retries + 1} 次",
                retry_count=retries + 1,
            )
        finally:
            db.close()
    if retries < max_retries:
        countdown = 5 * (retries + 1)
        log_event(
            user_id=user_id,
            module="async_task",
            action=f"{action_prefix}_retrying",
            target_type=target_type,
            target_id=target_id,
            detail=background_error_detail(self.request.id, retry=retries + 1, countdown=countdown),
        )
        raise self.retry(exc=exc, countdown=countdown, max_retries=max_retries)

    log_event(
        user_id=user_id,
        module="async_task",
        action=f"{action_prefix}_failed",
        target_type=target_type,
        target_id=target_id,
        detail=background_error_detail(self.request.id, retries=retries),
    )
    if target_type == "document":
        db = session_factory()
        try:
            document_jobs.mark_failed(
                self.request.id,
                db,
                error_message=sanitize_background_error_message(str(exc)),
                message="任务执行失败",
                retry_count=retries,
            )
        finally:
            db.close()
    raise exc


def retry_connector_sync(
    self,
    exc: Exception,
    *,
    job: ConnectorSyncJob,
    connector: ExternalConnector,
    log_event=log_async_task_event,
    session_factory=SessionLocal,
) -> None:
    retries = int(getattr(self.request, "retries", 0) or 0)
    max_retries = 2
    db = session_factory()
    try:
        current_job = db.query(ConnectorSyncJob).filter(ConnectorSyncJob.id == job.id).first()
        if not current_job:
            raise exc
        if retries < max_retries:
            current_job.status = "running"
            current_job.result_summary = f"同步重试中，第 {retries + 1} 次"
            current_job.error_message = sanitize_background_error_message(str(exc))
            db.commit()
            countdown = 5 * (retries + 1)
            log_event(
                user_id=current_job.user_id,
                module="async_task",
                action="connector_sync_retrying",
                target_type="connector_sync_job",
                target_id=current_job.id,
                detail=background_error_detail(self.request.id, retry=retries + 1, countdown=countdown),
            )
            raise self.retry(exc=exc, countdown=countdown, max_retries=max_retries)

        current_job.status = "failed"
        current_job.result_summary = f"{connector.name} 同步失败"
        current_job.error_message = sanitize_background_error_message(str(exc))
        db.commit()
        log_event(
            user_id=current_job.user_id,
            module="async_task",
            action="connector_sync_failed",
            target_type="connector_sync_job",
            target_id=current_job.id,
            detail=background_error_detail(self.request.id, retries=retries),
        )
        raise exc
    finally:
        db.close()
