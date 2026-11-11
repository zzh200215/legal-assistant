"""代理到期提醒任务：每天扫描代理有效期，提前 2 天提醒原收件人。"""

from app.core.celery_app import celery_app
from app.core.database import SessionLocal
from app.tasks.runtime import (
    beat_lock as _beat_lock,
    record_beat_heartbeat as _record_beat_heartbeat,
)


@celery_app.task(name="check_delegate_expiring")
@_beat_lock(task_name="check_delegate_expiring", ttl_seconds=3600)
def check_delegate_expiring_task():
    """扫描 delegate_end 在 2 天内的通知偏好，提醒原收件人一次。"""
    _record_beat_heartbeat()
    from app.services.jobs.delegate_reminders import scan_delegate_expiring

    db = SessionLocal()
    try:
        return scan_delegate_expiring(db)
    finally:
        db.close()
