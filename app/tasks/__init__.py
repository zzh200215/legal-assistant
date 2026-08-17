"""Celery 任务包：各域任务已拆至子模块，本模块负责聚合注册。

worker 经 conf.imports=("app.tasks",) 导入本包，进而导入各域任务子模块完成注册；
应用进程经 main.py 显式 import app.tasks。子模块的 Celery 任务在本模块 re-export，
保持 ``from app.tasks import <task>`` 的既有接口不变。
"""
from app.tasks import ops_tasks as _ops_tasks  # noqa: E402,F401

from app.tasks.document_tasks import (
    analyze_document_task,
    document_chunk_task,
    document_export_task,
    document_index_task,
    parse_document_task,
    recover_stale_document_jobs_task,
    summarize_document_task,
)
from app.tasks.notification_tasks import (
    deliver_email_send_requests_task,
    dispatch_notification_events_task,
    recover_stale_outbox_claims_task,
    retry_failed_webhook_deliveries_task,
)
from app.tasks.legal_tasks import (
    check_legal_approval_timeouts_task,
    check_legal_deadline_reminders_task,
    parse_contract_versions_task,
    process_open_contract_review_task,
    recover_queued_open_contract_reviews_task,
    scan_contract_expiry_alerts_task,
    scan_expired_portal_links_task,
)
from app.tasks.billing_tasks import (
    dispatch_payment_events_task,
    recover_stale_payment_events_task,
    recover_stale_reconciliation_runs_task,
    run_daily_reconciliation_task,
    scan_expired_subscriptions_task,
    scan_overdue_invoices_task,
)
from app.tasks.integration_tasks import (
    confirm_account_deletions_task,
    connector_sync_task,
    create_pilot_backup_task,
    dispatch_feishu_reminders_task,
    mailbox_sync_task,
    recover_stale_connector_syncs_task,
    recover_stale_mailbox_syncs_task,
)
from app.tasks.ops_tasks import (
    dispatch_operational_alerts_task,
    run_database_archive_task,
)


# 任务运行台账信号：随 app.tasks 包加载（worker 与应用进程均生效），
# 打破 celery_app → app.tasks 的模块级循环依赖后，signals 在此统一注册。
import app.tasks.signals  # noqa: E402,F401
