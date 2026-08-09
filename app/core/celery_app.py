from celery import Celery
from celery.schedules import crontab
from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "ai_office",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=240,
    task_soft_time_limit=210,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    imports=("app.tasks",),
    beat_schedule={
        "dispatch-scheduled-workflows": {
            "task": "dispatch_scheduled_workflows",
            "schedule": 60.0,
        },
        "dispatch-operational-alerts": {
            "task": "dispatch_operational_alerts",
            "schedule": 300.0,
        },
        "purge-mailbox-retention": {
            "task": "purge_mailbox_retention",
            "schedule": 86400.0,
        },
        "check-legal-approval-timeouts": {
            "task": "check_legal_approval_timeouts",
            "schedule": 300.0,
        },
        "check-legal-deadline-reminders": {
            "task": "check_legal_deadline_reminders",
            "schedule": 900.0,  # 每15分钟
        },
        "dispatch-notification-events": {
            "task": "dispatch_notification_events",
            "schedule": 60.0,
        },
        "scan-overdue-invoices": {
            "task": "scan_overdue_invoices",
            "schedule": 3600.0,  # 每小时
        },
        "scan-expired-portal-links": {
            "task": "scan_expired_portal_links",
            "schedule": 3600.0,
        },
        "scan-expired-subscriptions": {
            "task": "scan_expired_subscriptions",
            "schedule": 3600.0,  # 每小时
        },
        "scan-contract-expiry-alerts": {
            "task": "scan_contract_expiry_alerts",
            "schedule": 86400.0,  # 每天
        },
        "retry-failed-webhook-deliveries": {
            "task": "retry_failed_webhook_deliveries",
            "schedule": 300.0,  # 每5分钟
        },
        "parse-contract-versions": {
            "task": "parse_contract_versions",
            "schedule": 300.0,
        },
        "confirm-account-deletions": {
            "task": "confirm_account_deletions",
            "schedule": 86400.0,  # 每天
        },
        "create-pilot-backup": {
            "task": "create_pilot_backup",
            "schedule": crontab(minute=0, hour=2),  # 每日 02:00 全量备份
        },
        "dispatch-feishu-reminders": {
            "task": "dispatch_feishu_reminders",
            "schedule": crontab(minute=0, hour=9),  # 每日 09:00 飞书提醒（激活/周报）
        },
        "run-database-archive": {
            "task": "run_database_archive",
            "schedule": 86400.0,  # 每天：按保留策略清理过期日志/用量记录（默认 dry-run）
        },
    },
)

# 自动发现 tasks 模块
celery_app.autodiscover_tasks(["app.tasks"])

# 确保任务在 worker 与应用进程中都能稳定注册。
import app.tasks  # noqa: E402,F401
