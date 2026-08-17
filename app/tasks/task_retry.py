from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.observability import log_async_task_event
from app.core.observability_sanitizer import sanitize_background_error_message
from app.services.documents.document_job_service import document_job_service
from app.tasks.runtime import background_error_detail
def retry_task(
    self,
    exc: Exception,
    *,
    user_id: int | None,
    target_type: str,
    target_id: int | None,
    action_prefix: str,
    max_retries: int | None = None,
    backoff_base: int | None = None,
    log_event=log_async_task_event,
    session_factory=SessionLocal,
    document_jobs=document_job_service,
):
    settings = get_settings()
    if max_retries is None:
        max_retries = settings.DOCUMENT_TASK_MAX_RETRIES
    if backoff_base is None:
        backoff_base = settings.DOCUMENT_TASK_BACKOFF_BASE_SECONDS
    retries = int(getattr(self.request, "retries", 0) or 0)
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
        countdown = backoff_base * (retries + 1)
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

