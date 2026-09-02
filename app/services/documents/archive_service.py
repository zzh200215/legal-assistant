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

P1 审计保留（安全策略）：
- 审计类表（admin_audit_logs / security_audit_events / login_logs）默认不物理删除：
  过期行按 OBS_AUDIT_RETENTION_DAYS_JSON 的 retention_class 计算期限，
  OBS_AUDIT_ARCHIVE_ENABLED=true 时流式归档到 OBS_AUDIT_ARCHIVE_DIR（JSONL + 清单），
  仅 OBS_AUDIT_PURGE_AFTER_ARCHIVE=true 时才删除已归档行；归档行为本身入审计。
- 预聚合表 ops_metric_* 按 OBS_METRICS_SNAPSHOT_RETENTION_DAYS /
  OBS_AGGREGATION_*_RETENTION_DAYS 物理清理（可重建数据，允许直接删除）。

内容类保留（各有专属配置，不走 DATABASE_ARCHIVE_RETENTION_DAYS_JSON）：
- 邮箱镜像 mailbox_messages / mailbox_attachments 按 MAILBOX_RETENTION_DAYS 清理，
  含对象存储 blob（附件按内容哈希去重，多行可共享一个 key，仅无引用时才删 blob）。
- 邮件死信 email_send_requests(status=dead_letter) 按 EMAIL_DEAD_LETTER_RETENTION_DAYS 清理。
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, inspect
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.observability_sanitizer import sanitize_background_error_message
from app.models.archive import DatabaseArchiveRun
from app.models.auth_log import AdminAuditLog, LoginLog
from app.models.email import EmailAttachment, EmailSendRequest
from app.models.legal_notifications import LegalNotificationEvent, SecurityAuditEvent
from app.models.legal_platform import WebhookDelivery
from app.models.llm_call_log import LLMCallLog
from app.models.mailbox import MailboxAttachment, MailboxMessage
from app.models.operation_log import OperationLog
from app.models.ops_metric import OpsMetricDaily, OpsMetricHourly, OpsMetricSnapshot
from app.models.token_usage import TokenUsage
from app.models.ws_event_log import WsEventLog
from app.services.observability.oplog_service import oplog_service

logger = logging.getLogger(__name__)

# = outbound_email_service.EMAIL_REQ_DEAD_LETTER；此处复制字面量，
# 避免 archive -> notification 的模块依赖。
_EMAIL_DEAD_LETTER_STATUS = "dead_letter"


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
    "security_audit_events": (SecurityAuditEvent, "occurred_at"),
}

# 审计类表：保留任务按 OBS_AUDIT_RETENTION_DAYS_JSON 分级处理（归档而非默认物理删除）。
AUDIT_ARCHIVE_TABLES = frozenset({"admin_audit_logs", "security_audit_events", "login_logs"})


def _audit_retention_days(table: str) -> int:
    """审计表保留期限：OBS_AUDIT_TABLE_RETENTION_CLASS_JSON 映射到
    OBS_AUDIT_RETENTION_DAYS_JSON 的 retention_class；缺省 default=180。"""
    settings = get_settings()
    try:
        class_map = json.loads(settings.OBS_AUDIT_TABLE_RETENTION_CLASS_JSON or "{}")
        days_map = json.loads(settings.OBS_AUDIT_RETENTION_DAYS_JSON or "{}")
    except (TypeError, ValueError):
        class_map, days_map = {}, {}
    retention_class = class_map.get(table, "default")
    try:
        return int(days_map.get(retention_class, 180))
    except (TypeError, ValueError):
        return 180


def _serialize_audit_row(row: Any, table: str) -> dict:
    """审计行序列化（字段均已脱敏/哈希；任何值 JSON 化失败走 str 兜底）。"""
    payload: dict = {}
    for column in inspect(row).mapper.column_attrs:
        payload[column.key] = getattr(row, column.key, None)
    payload["archived_table"] = table
    return payload


class ArchiveService:
    def run(self, db: Session, *, dry_run: bool | None = None) -> dict:
        """执行一次归档。默认受 DATABASE_ARCHIVE_ENABLED/DRY_RUN 配置约束。"""
        settings = get_settings()
        if not settings.DATABASE_ARCHIVE_ENABLED:
            return {"enabled": False, "dry_run": True, "tables": {}}

        effective_dry_run = settings.DATABASE_ARCHIVE_DRY_RUN if dry_run is None else dry_run
        retention = settings.archive_retention_days()
        results: dict[str, dict] = {}
        # 预聚合表保留（独立配置，物理清理可重建数据）。
        results["ops_metrics"] = self._cleanup_ops_metrics(db, dry_run=effective_dry_run)
        # 内容类保留（独立配置：邮箱镜像 / 邮件死信）。
        results["mailbox"] = self._cleanup_mailbox(db, dry_run=effective_dry_run)
        results["email_dead_letters"] = self._cleanup_email_dead_letters(db, dry_run=effective_dry_run)
        for table, days in retention.items():
            if table not in ARCHIVE_TABLES:
                logger.warning("archive: 表 %s 不在归档白名单，跳过", table)
                results[table] = {"status": "unknown_table"}
                continue
            if table in AUDIT_ARCHIVE_TABLES:
                results[table] = self._archive_audit_table(db, table, dry_run=effective_dry_run)
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

    def _archive_audit_table(self, db: Session, table: str, *, dry_run: bool) -> dict:
        """审计类表：默认保留不删；OBS_AUDIT_ARCHIVE_ENABLED 时流式归档，
        仅 OBS_AUDIT_PURGE_AFTER_ARCHIVE=true 才删除已归档行。归档行为入审计。"""
        days = _audit_retention_days(table)
        model, time_col = ARCHIVE_TABLES[table]
        cutoff = _utcnow() - timedelta(days=days)
        run = self._start_run(db, table, cutoff, dry_run)
        if run is None:
            return {"status": "skipped_locked", "table": table}
        settings = get_settings()
        if not settings.OBS_AUDIT_ARCHIVE_ENABLED:
            # 审计默认不可直接物理删除：保留（不清理），仅记账。
            run.status = "completed"
            run.processed_count = 0
            run.deleted_count = 0
            run.finished_at = _utcnow()
            db.commit()
            self._audit_log(db, table, run)
            return {"status": "retained_by_policy", "table": table, "retention_days": days}
        try:
            archived = self._archive_rows(db, model, time_col, cutoff, dry_run, run.batch_size, table, run.id)
            deleted = 0
            if not dry_run and settings.OBS_AUDIT_PURGE_AFTER_ARCHIVE:
                deleted = self._purge_archived(db, model, time_col, cutoff, run.batch_size)
            run.status = "completed"
            run.processed_count = archived
            run.deleted_count = deleted
            run.finished_at = _utcnow()
            db.commit()
            self._audit_log(db, table, run)
            self._audit_archive_event(db, table, run, archived, deleted)
            return {"status": "archived", "table": table, "archived": archived,
                    "deleted": deleted, "retention_days": days, "purge_after_archive": settings.OBS_AUDIT_PURGE_AFTER_ARCHIVE}
        except Exception as exc:  # noqa: BLE001 - 归档失败不阻断其他表/主流程
            db.rollback()
            run = db.query(DatabaseArchiveRun).filter(DatabaseArchiveRun.id == run.id).first()
            if run is not None:
                run.status = "failed"
                run.error_message = sanitize_background_error_message(str(exc))
                run.finished_at = _utcnow()
                db.commit()
            logger.warning("archive: 审计表 %s 归档失败: %s", table, type(exc).__name__)
            return {"status": "failed", "table": table, "error": sanitize_background_error_message(str(exc))}

    def _archive_rows(self, db: Session, model, time_col: str, cutoff: datetime,
                      dry_run: bool, batch_size: int, table: str, run_id: int) -> int:
        """流式归档过期审计行到 JSONL + 清单（含 sha256），标记 archived_at。

        dry_run 时只统计不写文件、不改数据。
        """
        settings = get_settings()
        time_attr = getattr(model, time_col)
        if dry_run:
            return (
                db.query(model.id)
                .filter(time_attr < cutoff, getattr(model, "archived_at").is_(None))
                .count()
            )
        archive_dir = Path(settings.OBS_AUDIT_ARCHIVE_DIR)
        archive_dir.mkdir(parents=True, exist_ok=True)
        data_path = archive_dir / f"{table}_{run_id}.jsonl"
        hasher = hashlib.sha256()
        archived = 0
        last_id = 0
        with data_path.open("w", encoding="utf-8") as fh:
            while True:
                q = db.query(model).filter(
                    time_attr < cutoff,
                    model.id > last_id,
                    getattr(model, "archived_at").is_(None),
                )
                batch = q.order_by(model.id).limit(batch_size).all()
                if not batch:
                    break
                for row in batch:
                    line = json.dumps(_serialize_audit_row(row, table), ensure_ascii=False, default=str)
                    fh.write(line + "\n")
                    hasher.update((line + "\n").encode("utf-8"))
                    row.archived_at = _utcnow()
                    db.add(row)
                    archived += 1
                    last_id = row.id
                db.commit()
        manifest = {
            "format": "audit-archive-v1",
            "table": table,
            "run_id": run_id,
            "cutoff": cutoff.isoformat(),
            "generated_at": _utcnow().isoformat(),
            "record_count": archived,
            "file": data_path.name,
            "sha256": hasher.hexdigest(),
            "note": "归档文件仅含脱敏/哈希字段；审计默认不物理删除",
        }
        (archive_dir / f"{table}_{run_id}.manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return archived

    def _purge_archived(self, db: Session, model, time_col: str, cutoff: datetime, batch_size: int) -> int:
        """受控清理：仅删除已归档（archived_at 非空）且过期的行。"""
        time_attr = getattr(model, time_col)
        deleted = 0
        last_id = 0
        while True:
            ids = [
                row[0]
                for row in db.query(model.id)
                .filter(time_attr < cutoff, model.id > last_id,
                        getattr(model, "archived_at").isnot(None))
                .order_by(model.id)
                .limit(batch_size)
                .all()
            ]
            if not ids:
                break
            last_id = ids[-1]
            db.query(model).filter(model.id.in_(ids)).delete(synchronize_session=False)
            db.commit()
            deleted += len(ids)
        return deleted

    def _cleanup_ops_metrics(self, db: Session, *, dry_run: bool) -> dict:
        """预聚合/快照表保留（物理清理可重建，幂等）。按 bucket_start（数据时间）判定。"""
        settings = get_settings()
        now = _utcnow()
        specs = [
            (OpsMetricSnapshot, settings.OBS_METRICS_SNAPSHOT_RETENTION_DAYS),
            (OpsMetricHourly, settings.OBS_AGGREGATION_HOURLY_RETENTION_DAYS),
            (OpsMetricDaily, settings.OBS_AGGREGATION_DAILY_RETENTION_DAYS),
        ]
        result: dict[str, dict] = {}
        for model, days in specs:
            cutoff = now - timedelta(days=days)
            count = db.query(model).filter(model.bucket_start < cutoff).count()
            if not dry_run and count:
                db.query(model).filter(model.bucket_start < cutoff).delete(synchronize_session=False)
                db.commit()
            result[model.__tablename__] = {"retention_days": days, "expired": count,
                                           "deleted": count if not dry_run else 0}
        # WS 事件日志（断线恢复源）：resume 窗口 24h 后即可物理清理（幂等可重建）
        ws_days = settings.OBS_WS_EVENT_RETENTION_DAYS
        ws_cutoff = now - timedelta(days=ws_days)
        ws_count = db.query(WsEventLog).filter(WsEventLog.expires_at < ws_cutoff).count()
        if not dry_run and ws_count:
            db.query(WsEventLog).filter(WsEventLog.expires_at < ws_cutoff).delete(synchronize_session=False)
            db.commit()
        result["ws_event_logs"] = {"retention_days": ws_days, "expired": ws_count,
                                   "deleted": ws_count if not dry_run else 0}
        return result

    def _cleanup_mailbox(self, db: Session, *, dry_run: bool) -> dict:
        """邮箱镜像保留（MAILBOX_RETENTION_DAYS）：过期邮件连同附件行与对象存储 blob 一并清理。

        - 时间基准 coalesce(received_at, created_at)：收件时间缺失时退回落库时间。
        - 先删 blob 再删行：中途失败时行还在，下一轮按同一 key 重试，不留无主对象。
        - 附件按内容哈希去重，多行可共享一个 storage_key：仅当批外再无引用时才删 blob。
        - 先删附件行再删邮件行（mailbox_attachments.message_id 外键）。
        """
        days = int(get_settings().MAILBOX_RETENTION_DAYS)
        cutoff = _utcnow() - timedelta(days=days)
        table = MailboxMessage.__tablename__
        run = self._start_run(db, table, cutoff, dry_run)
        if run is None:
            return {"status": "skipped_locked", "table": table}
        batch_size = run.batch_size
        received = func.coalesce(MailboxMessage.received_at, MailboxMessage.created_at)
        processed = deleted = attachments = blobs = blob_failures = 0
        last_id = 0
        try:
            while True:
                ids = [
                    row[0]
                    for row in db.query(MailboxMessage.id)
                    .filter(received < cutoff, MailboxMessage.id > last_id)
                    .order_by(MailboxMessage.id)
                    .limit(batch_size)
                    .all()
                ]
                if not ids:
                    break
                last_id = ids[-1]
                processed += len(ids)
                if dry_run:
                    continue
                purged, failed = self._purge_blobs(self._orphan_blob_keys(
                    db, MailboxAttachment, MailboxAttachment.message_id, ids))
                blobs += purged
                blob_failures += failed
                attachments += (
                    db.query(MailboxAttachment)
                    .filter(MailboxAttachment.message_id.in_(ids))
                    .delete(synchronize_session=False)
                )
                deleted += (
                    db.query(MailboxMessage)
                    .filter(MailboxMessage.id.in_(ids))
                    .delete(synchronize_session=False)
                )
                db.commit()
        except Exception as exc:  # noqa: BLE001 - 清理失败不阻断其他表/主流程
            return self._fail_run(db, run.id, table, exc)
        self._finish_run(db, table, run, processed=processed, deleted=deleted)
        return {"status": "completed", "table": table, "retention_days": days,
                "processed": processed, "deleted": deleted,
                "attachments_deleted": attachments, "blobs_deleted": blobs,
                "blob_failures": blob_failures, "dry_run": dry_run}

    def _cleanup_email_dead_letters(self, db: Session, *, dry_run: bool) -> dict:
        """邮件死信保留（EMAIL_DEAD_LETTER_RETENTION_DAYS）：清理过期的投递死信台账行。

        边界（有意为之，不在这条策略内）：
        - 仍被通知事件引用（legal_notification_events.email_send_request_id）的死信跳过，
          等事件按自身保留策略清理后的下一轮再删——否则会丢掉事件失败原因的追溯链。
        - 附件与草稿正文属用户内容（email_drafts / email_attachments），只解除
          send_request_id 引用，不删除行、不删 blob。
        """
        days = int(get_settings().EMAIL_DEAD_LETTER_RETENTION_DAYS)
        cutoff = _utcnow() - timedelta(days=days)
        table = EmailSendRequest.__tablename__
        run = self._start_run(db, table, cutoff, dry_run)
        if run is None:
            return {"status": "skipped_locked", "table": table}
        batch_size = run.batch_size
        failed_at = func.coalesce(EmailSendRequest.dead_letter_at, EmailSendRequest.updated_at,
                                  EmailSendRequest.created_at)
        processed = deleted = skipped = unlinked = 0
        last_id = 0
        try:
            while True:
                ids = [
                    row[0]
                    for row in db.query(EmailSendRequest.id)
                    .filter(EmailSendRequest.status == _EMAIL_DEAD_LETTER_STATUS,
                            failed_at < cutoff, EmailSendRequest.id > last_id)
                    .order_by(EmailSendRequest.id)
                    .limit(batch_size)
                    .all()
                ]
                if not ids:
                    break
                last_id = ids[-1]
                processed += len(ids)
                referenced = {
                    row[0]
                    for row in db.query(LegalNotificationEvent.email_send_request_id)
                    .filter(LegalNotificationEvent.email_send_request_id.in_(ids))
                    .all()
                }
                removable = [rid for rid in ids if rid not in referenced]
                skipped += len(ids) - len(removable)
                if dry_run or not removable:
                    continue
                unlinked += (
                    db.query(EmailAttachment)
                    .filter(EmailAttachment.send_request_id.in_(removable))
                    .update({EmailAttachment.send_request_id: None}, synchronize_session=False)
                )
                deleted += (
                    db.query(EmailSendRequest)
                    .filter(EmailSendRequest.id.in_(removable))
                    .delete(synchronize_session=False)
                )
                db.commit()
        except Exception as exc:  # noqa: BLE001 - 清理失败不阻断其他表/主流程
            return self._fail_run(db, run.id, table, exc)
        self._finish_run(db, table, run, processed=processed, deleted=deleted)
        return {"status": "completed", "table": table, "retention_days": days,
                "processed": processed, "deleted": deleted,
                "skipped_referenced": skipped, "attachments_unlinked": unlinked,
                "dry_run": dry_run}

    @staticmethod
    def _orphan_blob_keys(db: Session, model, owner_col, owner_ids: list[int]) -> set[str]:
        """批内附件的 storage_key 里，批外已无任何行引用的那些（删 blob 才安全）。"""
        keys = {
            row[0]
            for row in db.query(model.storage_key)
            .filter(owner_col.in_(owner_ids), model.storage_key.isnot(None))
            .all()
            if row[0]
        }
        if not keys:
            return set()
        shared = {
            row[0]
            for row in db.query(model.storage_key)
            .filter(model.storage_key.in_(list(keys)), owner_col.notin_(owner_ids))
            .all()
            if row[0]
        }
        return keys - shared

    @staticmethod
    def _purge_blobs(keys: Iterable[str]) -> tuple[int, int]:
        """删除对象存储 blob；单个失败只计数不抛（行未删，下一轮重试同一 key）。"""
        from app.services.storage.storage_service import storage_service

        purged = failures = 0
        for key in keys:
            try:
                storage_service.delete(key)
                purged += 1
            except Exception as exc:  # noqa: BLE001 - 单个对象删除失败不阻断整轮清理
                failures += 1
                logger.warning("archive: 附件对象删除失败 (%s)", type(exc).__name__)
        return purged, failures

    def _finish_run(self, db: Session, table: str, run: DatabaseArchiveRun, *,
                    processed: int, deleted: int) -> None:
        run.status = "completed"
        run.processed_count = processed
        run.deleted_count = deleted
        run.finished_at = _utcnow()
        db.commit()
        self._audit_log(db, table, run)

    def _fail_run(self, db: Session, run_id: int, table: str, exc: Exception) -> dict:
        db.rollback()
        run = db.query(DatabaseArchiveRun).filter(DatabaseArchiveRun.id == run_id).first()
        if run is not None:
            run.status = "failed"
            run.error_message = sanitize_background_error_message(str(exc))
            run.finished_at = _utcnow()
            db.commit()
        logger.warning("archive: %s 清理失败: %s", table, type(exc).__name__)
        return {"status": "failed", "table": table,
                "error": sanitize_background_error_message(str(exc))}

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

    def _audit_archive_event(self, db: Session, table: str, run: DatabaseArchiveRun,
                             archived: int, deleted: int) -> None:
        """归档行为本身写安全审计（export 类；写失败降级记录，不吞错）。"""
        try:
            from app.services.org.security_audit_service import write_event

            write_event(
                event_type="export",
                actor_type="system",
                actor_id="archive_task",
                result="success",
                action="audit_archive",
                target_type="database_archive_run",
                target_id=str(run.id),
                reason_code="audit_retention_archive",
                sanitized_metadata=json.dumps(
                    {"table": table, "archived": archived, "deleted": deleted}, ensure_ascii=False
                ),
                db=db,
            )
        except Exception as exc:  # noqa: BLE001 - 归档审计写失败按 degrade 记录
            logger.warning("archive: 审计归档事件写入失败 table=%s: %s", table, type(exc).__name__)


archive_service = ArchiveService()
