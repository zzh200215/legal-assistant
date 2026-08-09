"""大表归档 / 保留策略：按表保留天数批量清理过期记录。

设计约束（见任务要求）：
- 默认关闭 + 默认 dry-run：开发环境永不真实删除；生产也须显式
  DATABASE_ARCHIVE_ENABLED=true 且 DATABASE_ARCHIVE_DRY_RUN=false。
- 按表配置保留天数（DATABASE_ARCHIVE_RETENTION_DAYS_JSON）。
- 批量处理：按 id 游标分批，绝不全量载入内存。
- 幂等：删除以 created_at < cutoff 且 id > last_id 推进；重复执行不会重复删除
  （已删行不存在），可随时中断、下一轮定时任务续跑。
- 运行锁：database_archive_runs 台账行作为锁；存在未过期的 running 行则跳过，
  超过 DATABASE_ARCHIVE_LOCK_TIMEOUT_MINUTES 视为陈旧可抢占。
- 可审计：每表一次台账行 + 一条 OperationLog。
- 失败可重试：异常时台账标记 failed（错误脱敏），任务不抛错。
- 外键：清理对象均为叶子表（日志/用量/通知/投递），无子表引用，删除安全。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.observability_sanitizer import sanitize_background_error_message
from app.models.archive import DatabaseArchiveRun
from app.models.auth_log import AdminAuditLog, LoginLog
from app.models.legal_notifications import LegalNotificationEvent, SecurityAuditEvent
from app.models.legal_platform import WebhookDelivery
from app.models.llm_call_log import LLMCallLog
from app.models.operation_log import OperationLog
from app.models.schedule import WorkflowExecution
from app.models.token_usage import TokenUsage
from app.services.oplog_service import oplog_service

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """SQLite 不保留时区，统一使用 naive UTC，避免 naive/aware 混用报错。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)

# 可归档表注册表：表名 -> (ORM模型, 时间列)。
# SecurityAuditEvent 用 occurred_at 而非 created_at。
ARCHIVE_TABLES: dict[str, tuple[Any, str]] = {
    "operation_logs": (OperationLog, "created_at"),
    "token_usage": (TokenUsage, "created_at"),
    "llm_call_logs": (LLMCallLog, "created_at"),
    "login_logs": (LoginLog, "created_at"),
    "admin_audit_logs": (AdminAuditLog, "created_at"),
    "legal_notification_events": (LegalNotificationEvent, "created_at"),
    "webhook_deliveries": (WebhookDelivery, "created_at"),
    "workflow_executions": (WorkflowExecution, "created_at"),
    "security_audit_events": (SecurityAuditEvent, "occurred_at"),
}


class ArchiveService:
    def run(self, db: Session, *, dry_run: bool | None = None) -> dict:
        """执行一次归档。默认受 DATABASE_ARCHIVE_ENABLED/DRY_RUN 配置约束。"""
        settings = get_settings()
        if not settings.DATABASE_ARCHIVE_ENABLED:
            return {"enabled": False, "dry_run": True, "tables": {}}

        effective_dry_run = settings.DATABASE_ARCHIVE_DRY_RUN if dry_run is None else dry_run
        retention = settings.archive_retention_days()
        results: dict[str, dict] = {}
        for table, days in retention.items():
            if table not in ARCHIVE_TABLES:
                logger.warning("archive: 表 %s 不在归档白名单，跳过", table)
                results[table] = {"status": "unknown_table"}
                continue
            results[table] = self._archive_table(db, table, days, dry_run=effective_dry_run)
        return {"enabled": True, "dry_run": effective_dry_run, "tables": results}

    def _archive_table(self, db: Session, table: str, days: int, *, dry_run: bool) -> dict:
        model, time_col = ARCHIVE_TABLES[table]
        cutoff = _utcnow() - timedelta(days=days)
        run = self._start_run(db, table, cutoff, dry_run)
        if run is None:
            return {"status": "skipped_locked", "table": table}
        try:
            result = self._purge(db, model, time_col, cutoff, dry_run, run.batch_size)
            run.status = "completed"
            run.processed_count = result["processed"]
            run.deleted_count = result["deleted"]
            run.finished_at = _utcnow()
            db.commit()
            self._audit_log(db, table, run)
            return {**result, "status": "completed"}
        except Exception as exc:  # noqa: BLE001 - 归档失败不阻断其他表/主流程
            db.rollback()
            run = db.query(DatabaseArchiveRun).filter(DatabaseArchiveRun.id == run.id).first()
            if run is not None:
                run.status = "failed"
                run.error_message = sanitize_background_error_message(str(exc))
                run.finished_at = _utcnow()
                db.commit()
            logger.warning("archive: 表 %s 归档失败: %s", table, type(exc).__name__)
            return {"status": "failed", "table": table, "error": sanitize_background_error_message(str(exc))}

    def _start_run(self, db: Session, table: str, cutoff: datetime, dry_run: bool) -> DatabaseArchiveRun | None:
        """台账行即运行锁；返回 None 表示表被并发运行锁定。"""
        settings = get_settings()
        now = _utcnow()
        active = (
            db.query(DatabaseArchiveRun)
            .filter(DatabaseArchiveRun.table_name == table, DatabaseArchiveRun.status == "running")
            .order_by(DatabaseArchiveRun.id.desc())
            .first()
        )
        if active is not None:
            started = active.started_at or now
            elapsed = (now - started).total_seconds()
            if elapsed < settings.DATABASE_ARCHIVE_LOCK_TIMEOUT_MINUTES * 60:
                return None
            active.status = "failed"
            active.error_message = "陈旧运行（超过锁超时）被抢占"
        run = DatabaseArchiveRun(
            table_name=table,
            status="running",
            dry_run=dry_run,
            cutoff=cutoff,
            batch_size=settings.DATABASE_ARCHIVE_BATCH_SIZE,
            started_at=now,
        )
        db.add(run)
        db.commit()
        return run

    def _purge(self, db: Session, model, time_col: str, cutoff: datetime, dry_run: bool, batch_size: int) -> dict:
        """按 id 游标分批处理过期记录；dry_run 时只统计不删除。"""
        time_attr = getattr(model, time_col)
        last_id = 0
        processed = 0
        deleted = 0
        while True:
            batch = (
                db.query(model.id)
                .filter(time_attr < cutoff, model.id > last_id)
                .order_by(model.id)
                .limit(batch_size)
                .all()
            )
            if not batch:
                break
            ids = [row[0] for row in batch]
            last_id = ids[-1]
            processed += len(ids)
            if not dry_run:
                db.query(model).filter(model.id.in_(ids)).delete(synchronize_session=False)
                db.commit()
                deleted += len(ids)
        return {
            "table": model.__tablename__,
            "cutoff": cutoff.isoformat(),
            "processed": processed,
            "deleted": deleted,
            "dry_run": dry_run,
        }

    def _audit_log(self, db: Session, table: str, run: DatabaseArchiveRun) -> None:
        try:
            oplog_service.log(
                module="archive",
                action=f"archive_{table}_purged",
                db=db,
                user_id=None,
                target_type="database_archive_run",
                target_id=run.id,
                detail=(
                    f"table={table}; processed={run.processed_count}; deleted={run.deleted_count}; "
                    f"dry_run={run.dry_run}; cutoff={run.cutoff.isoformat()}"
                ),
            )
        except Exception:  # noqa: BLE001 - 审计失败不影响归档
            logger.warning("archive: 表 %s 审计日志写入失败", table)


archive_service = ArchiveService()
