"""任务到期提醒任务：每 15 分钟扫描到期前 1 天 / 当天任务。"""

from app.core.celery_app import celery_app
from app.core.database import SessionLocal
from app.tasks.runtime import (
    beat_lock as _beat_lock,
    record_beat_heartbeat as _record_beat_heartbeat,
)


@celery_app.task(name="check_task_due_reminders")
@_beat_lock(task_name="check_task_due_reminders", ttl_seconds=1800)
def check_task_due_reminders_task():
    """扫描任务并发「明天到期 / 今天到期」站内+邮件提醒（同任务同阶段只发一次）。"""
    _record_beat_heartbeat()
    from app.services.jobs.task_reminders import scan_task_due_reminders

    db = SessionLocal()
    try:
        return scan_task_due_reminders(db)
    finally:
        db.close()
