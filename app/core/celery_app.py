from celery import Celery
from celery.schedules import crontab
from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "ai_office",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

# ── 队列拆分与路由（P1）：5 队列 + 每队列专属超时 ──────────────────────────
# Redis broker 优先级仅对未预取消息生效、单 worker 队头阻塞，故不启用优先级，
# 用「独立队列 + 每队列专属 worker/concurrency」实现同等隔离（见 operations-runbook）。
_QUEUE_LIMITS = {
    "llm": (300, 270),
    "document": (600, 540),
    "connector": (240, 210),
    "notification": (120, 100),
    "billing": (300, 270),
}


def _routes() -> dict:
    routes: dict[str, dict] = {}

    def add(queue: str, *names: str) -> None:
        hard, soft = _QUEUE_LIMITS[queue]
        for name in names:
            routes[name] = {"queue": queue, "time_limit": hard, "soft_time_limit": soft}

    add("document", "parse_document", "document_chunk", "document_index", "document_export",
        "recover_stale_document_jobs", "parse_contract_versions")
    add("llm", "summarize_document", "analyze_document", "process_open_contract_review")
    add("connector", "connector_sync_task", "recover_stale_connector_syncs",
        "retry_failed_webhook_deliveries", "dispatch_feishu_reminders",
        "dispatch_operational_alerts", "run_database_archive", "create_pilot_backup")
    add("notification", "dispatch_notification_events", "check_legal_deadline_reminders",
        "scan_expired_portal_links", "scan_contract_expiry_alerts",
        "check_legal_approval_timeouts", "confirm_account_deletions",
        "deliver_email_send_requests", "recover_stale_outbox_claims")
    add("billing", "scan_overdue_invoices", "scan_expired_subscriptions")
    add("connector", "mailbox_sync_task", "recover_stale_mailbox_syncs")
    return routes


celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    # 全局默认超时抬到 300/270；未显式路由任务落已消费队列（task_default_queue），
    # 不无意进默认 `celery` 队列。
    task_time_limit=300,
    task_soft_time_limit=270,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_default_queue=settings.TASK_DEFAULT_QUEUE,
    imports=("app.tasks",),
    beat_schedule={
        "dispatch-operational-alerts": {
            "task": "dispatch_operational_alerts",
            "schedule": 300.0,
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
        "deliver-email-send-requests": {
            "task": "deliver_email_send_requests",
            "schedule": 60.0,
        },
        "recover-stale-outbox-claims": {
            "task": "recover_stale_outbox_claims",
            "schedule": 300.0,
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
        "recover-stale-document-jobs": {
            "task": "recover_stale_document_jobs",
            "schedule": 300.0,  # 每5分钟：回收租约过期的文档处理任务并重新入队
        },
    },
)

if settings.TASK_QUEUE_ROUTING_ENABLED:
    celery_app.conf.task_routes = _routes()

# 自动发现 tasks 模块
celery_app.autodiscover_tasks(["app.tasks"])

# 确保任务在 worker 与应用进程中都能稳定注册。
import app.tasks  # noqa: E402,F401

# 连接器同步回收 beat：仅 CONNECTOR_SYNC_ENABLED 时注册（mock 连接器，默认关闭）。
if settings.CONNECTOR_SYNC_ENABLED:
    celery_app.conf.beat_schedule["recover-stale-connector-syncs"] = {
        "task": "recover_stale_connector_syncs",
        "schedule": 60.0,
    }

# 邮箱同步回收 beat：仅 MAILBOX_SYNC_ENABLED 时注册（mock 邮箱，默认关闭）。
if settings.MAILBOX_SYNC_ENABLED:
    celery_app.conf.beat_schedule["recover-stale-mailbox-syncs"] = {
        "task": "recover_stale_mailbox_syncs",
        "schedule": 60.0,
    }

# 任务运行台账信号：注册关键任务后自动写入 task_runs（未注册任务零开销）。
import app.tasks.signals  # noqa: E402,F401
