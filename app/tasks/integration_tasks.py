"""集成任务：账号注销/备份/飞书/连接器/邮箱同步。从 app.tasks.__init__ 拆出。"""
import json
import os
import subprocess
import sys
from pathlib import Path

from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.obs_context import enqueue_headers as obs_enqueue_headers
from app.core.observability import log_async_task_event
from app.core.observability_sanitizer import sanitize_background_error_message
from app.core.time import utc_now
from app.models.user import User
from app.tasks.runtime import (
    acquire_task_lock as _acquire_task_lock,
    beat_lock as _beat_lock,
    record_beat_heartbeat as _record_beat_heartbeat,
    release_task_lock as _release_task_lock,
)
from app.tasks.task_run_registry import TaskRunSpec as _TaskRunSpec
from app.tasks.task_run_registry import register as _register_task_run_spec


@celery_app.task(name="confirm_account_deletions")
@_beat_lock(task_name="confirm_account_deletions", ttl_seconds=86400)
def confirm_account_deletions_task():
    """Beat 任务：自动确认冷却期已满（默认30天）的账号注销请求，执行匿名化。"""
    _record_beat_heartbeat()
    db = SessionLocal()
    try:
        from app.services.auth.account_deletion_service import confirm_expired_pending

        confirmed_count = confirm_expired_pending(db)
        return {"confirmed_count": confirmed_count}
    finally:
        db.close()


@celery_app.task(name="create_pilot_backup")
@_beat_lock(task_name="create_pilot_backup", ttl_seconds=86400)
def create_pilot_backup_task():
    """Beat 任务：每日全量备份（数据库 + 本地数据目录），调度即授权 --confirm。

    仅支持 MySQL/PostgreSQL 驱动；sqlite（默认开发库）等驱动直接跳过。
    """
    _record_beat_heartbeat()
    settings = get_settings()
    database_url = settings.DATABASE_URL or ""
    driver = database_url.split(":", 1)[0]
    if not database_url or not driver.startswith(("mysql", "postgres")):
        return {"status": "skipped", "reason": f"unsupported_database_driver: {driver or 'empty'}"}
    script = Path(__file__).resolve().parents[2] / "scripts" / "create_pilot_backup.py"
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    command = [sys.executable, str(script), "--confirm", "--output-dir", settings.BACKUP_OUTPUT_DIR]
    for data_dir in settings.BACKUP_DATA_DIRS:
        command.extend(["--data-dir", data_dir])
    if settings.BACKUP_OFFSITE_DIR:
        command.extend(["--offsite-dir", settings.BACKUP_OFFSITE_DIR])
    command.extend(["--retention-count", str(settings.BACKUP_RETENTION_COUNT)])
    try:
        process = subprocess.run(command, capture_output=True, text=True, env=env, timeout=180)
    except (subprocess.SubprocessError, OSError) as exc:
        return {"status": "error", "message": sanitize_background_error_message(str(exc))}
    try:
        payload = json.loads(process.stdout or "{}")
    except json.JSONDecodeError:
        payload = {}
    if process.returncode == 0 and payload.get("status") == "ok":
        return {"status": "ok", "backup_dir": payload.get("backup_dir")}
    message = payload.get("message") or (process.stderr or "").strip() or f"exit_code={process.returncode}"
    return {"status": "error", "message": sanitize_background_error_message(message)}


@celery_app.task(name="dispatch_feishu_reminders")
@_beat_lock(task_name="dispatch_feishu_reminders", ttl_seconds=86400)
def dispatch_feishu_reminders_task():
    """M4：向已绑定飞书用户推送激活引导/周报回访卡片（出站未配置凭据时自动跳过）。"""
    _record_beat_heartbeat()
    db = SessionLocal()
    try:
        import asyncio

        from app.services.integration.feishu_service import dispatch_feishu_reminders

        return asyncio.run(dispatch_feishu_reminders(db))
    finally:
        db.close()


# ── 连接器同步（mock 连接器，CONNECTOR_SYNC_ENABLED 默认关闭）──────────────────

def _connector_context(db, connector_id: int, *_: int) -> dict:
    from app.models.connector import ExternalConnector

    conn = db.query(ExternalConnector).filter(ExternalConnector.id == int(connector_id)).first()
    if not conn:
        return {}
    user = db.query(User).filter(User.id == conn.user_id).first()
    if not user:
        return {}
    return {"tenant_id": user.organization_id, "user_id": user.id}


@celery_app.task(name="connector_sync_task")
def connector_sync_task(connector_id: int, sync_mode: str = "manual", trigger_id: int | None = None):
    """连接器同步任务：分布式锁 + SyncRun 台账 + 断点恢复（mock 连接器）。

    CONNECTOR_SYNC_ENABLED 关闭时跳过；未获锁时安全跳过（防多实例并发）。
    """
    settings = get_settings()
    if not settings.CONNECTOR_SYNC_ENABLED:
        return {"status": "disabled"}
    ttl = int(settings.SYNC_RUN_LEASE_TTL_SECONDS)
    token = _acquire_task_lock("connector_sync", scope=f"conn:{connector_id}", ttl_seconds=ttl)
    if token is None:
        log_async_task_event(
            user_id=None,
            module="async_task",
            action="connector_sync_skipped_lock",
            target_type="connector",
            target_id=connector_id,
            detail="lock held by another worker",
        )
        return {"status": "skipped", "reason": "lock_held"}
    try:
        from app.services.integration.connector_sync_framework import _run_connector_sync

        return _run_connector_sync(connector_id, sync_mode, trigger_id, token=token)
    finally:
        _release_task_lock("connector_sync", scope=f"conn:{connector_id}", token=token)


@celery_app.task(name="recover_stale_connector_syncs")
@_beat_lock(task_name="recover_stale_connector_syncs", ttl_seconds=180)
def recover_stale_connector_syncs_task():
    """Beat：回收租约过期的连接器同步 run 并重新入队（仅 CONNECTOR_SYNC_ENABLED 时调度）。"""
    _record_beat_heartbeat()
    settings = get_settings()
    if not settings.CONNECTOR_SYNC_ENABLED:
        return {"recovered": 0}
    from datetime import timedelta

    from app.models.sync_run import SyncRun

    db = SessionLocal()
    try:
        stale_before = utc_now() - timedelta(seconds=int(settings.SYNC_RUN_LEASE_TTL_SECONDS))
        stale_runs = db.query(SyncRun).filter(
            SyncRun.status == "running",
            SyncRun.lease_expires_at.isnot(None),
            SyncRun.lease_expires_at < stale_before,
        ).limit(50).all()
        recovered = 0
        for run in stale_runs:
            run.status = "pending"
            run.error_code = "lease_expired"
            db.commit()
            connector_sync_task.delay(run.connector_id, sync_mode="recover", headers=obs_enqueue_headers())
            recovered += 1
        return {"recovered": recovered}
    finally:
        db.close()


# connector_sync_task 的台账注册保持单一来源（其余关键任务在 task_run_registry.py 登记）。
_register_task_run_spec(_TaskRunSpec(
    task_name="connector_sync_task",
    queue="connector",
    business_key_fn=lambda connector_id, *_: f"connector:{int(connector_id)}",
    context_fn=_connector_context,
))


# ── 邮箱同步（mock 邮箱，MAILBOX_SYNC_ENABLED 默认关闭）────────────────────────

@celery_app.task(name="mailbox_sync_task")
def mailbox_sync_task(account_id: int, sync_mode: str = "manual"):
    """邮箱同步任务：UIDVALIDITY+UID 幂等 + 附件安全 + 断点恢复（mock 邮箱）。

    MAILBOX_SYNC_ENABLED 关闭时跳过；未获锁时安全跳过（防多实例并发）。
    """
    settings = get_settings()
    if not settings.MAILBOX_SYNC_ENABLED:
        return {"status": "disabled"}
    from datetime import timedelta

    from app.models.mailbox import MailboxSyncAccount
    from app.services.integration.mailbox_sync_service import mailbox_sync_service

    ttl = int(settings.SYNC_RUN_LEASE_TTL_SECONDS)
    token = _acquire_task_lock("mailbox_sync", scope=f"mailbox:{account_id}", ttl_seconds=ttl)
    if token is None:
        return {"status": "skipped", "reason": "lock_held"}
    db = SessionLocal()
    try:
        account = db.query(MailboxSyncAccount).filter(MailboxSyncAccount.id == account_id).first()
        if account is None:
            return {"status": "error", "reason": "account_not_found"}
        account.claimed_by = token
        account.claim_expires_at = utc_now() + timedelta(seconds=ttl)
        db.commit()
        return mailbox_sync_service.sync_account(db=db, account=account, owner=token)
    finally:
        db.close()
        _release_task_lock("mailbox_sync", scope=f"mailbox:{account_id}", token=token)


@celery_app.task(name="recover_stale_mailbox_syncs")
@_beat_lock(task_name="recover_stale_mailbox_syncs", ttl_seconds=180)
def recover_stale_mailbox_syncs_task():
    """Beat：回收租约过期的邮箱同步账户并重新入队（仅 MAILBOX_SYNC_ENABLED 时调度）。"""
    _record_beat_heartbeat()
    settings = get_settings()
    if not settings.MAILBOX_SYNC_ENABLED:
        return {"recovered": 0}
    from app.services.integration.mailbox_sync_service import mailbox_sync_service

    db = SessionLocal()
    try:
        accounts = mailbox_sync_service.recover_stale(db=db)
        for account in accounts:
            mailbox_sync_task.delay(account.id, sync_mode="recover", headers=obs_enqueue_headers())
        return {"recovered": len(accounts)}
    finally:
        db.close()
