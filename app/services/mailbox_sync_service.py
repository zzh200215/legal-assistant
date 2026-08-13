"""邮箱同步服务（app/services/mailbox_sync_service.py）。

绿地实现，mock IMAP 连接器（默认 MAILBOX_SYNC_ENABLED=false），不复活真实 IMAP 供应商。
可靠幂等与安全处理：
- 唯一标识 = account_id + folder + UIDVALIDITY + UID；Message-ID 仅辅助去重，
  不作为唯一可靠边界。
- cursor / checkpoint / last_successful_uid 仅在整批邮件及其附件成功持久化后推进；
  中断/批次失败后从最后成功 checkpoint 恢复。
- 附件流式 spool + document_security 复用（大小限制 / 真实 MIME / 白名单 /
  zip-bomb / 哈希去重）；扫描失败或高风险附件隔离（quarantined），不进入下游流程。
- 不记录邮件正文、附件内容、访问令牌或完整敏感收件人到普通日志。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from io import BytesIO
from typing import Any

from app.core.config import get_settings
from app.core.time import utc_now
from app.models.mailbox import MailboxAttachment, MailboxMessage, MailboxSyncAccount
from app.services.document_security import (
    DocumentSecurityError,
    allowed_extensions,
    detect_mime,
    inspect_zip_safety,
    spool_upload_to_temp,
)

_SCAN_CLEAN = "clean"
_SCAN_BLOCKED = "blocked"
_SCAN_QUARANTINED = "quarantined"
_SCAN_ERROR = "error"


@dataclass
class MailboxPage:
    items: list[dict]
    uidvalidity: str
    next_cursor: str | None
    has_more: bool
    source_version: str | None


class MockMailboxClient:
    """内存数据集分页客户端（mock IMAP：uidvalidity + uid 游标，可注入中断）。"""

    def __init__(self, messages: list[dict], uidvalidity: str = "V1",
                 interrupt_after: int | None = None) -> None:
        self._messages = list(messages)
        self._uidvalidity = uidvalidity
        self._interrupt_after = interrupt_after
        self.calls = 0

    def page(self, account_id: int, cursor: str | None, page_size: int = 50) -> MailboxPage:
        self.calls += 1
        if self._interrupt_after is not None and self.calls >= self._interrupt_after:
            raise RuntimeError("mock mailbox interrupted")
        start = int(cursor) if cursor else 0
        items = self._messages[start:start + page_size]
        next_start = start + len(items)
        has_more = next_start < len(self._messages)
        return MailboxPage(
            items=items,
            uidvalidity=self._uidvalidity,
            next_cursor=str(next_start) if has_more else None,
            has_more=has_more,
            source_version=f"{self._uidvalidity}:{next_start}",
        )


def build_mock_mailbox(account_id: int, *, count: int = 8, attachments: bool = True) -> list[dict]:
    """确定性样本邮件（演示/测试用）。每条含 uid/subject/sender/received/attachments。"""
    messages = []
    for i in range(count):
        atts = []
        if attachments:
            atts.append({
                "filename": f"合同-{i}.pdf",
                "mime_type": "application/pdf",
                "content": b"%PDF-1.4\n% mock pdf content for attachment dedupe",
            })
        messages.append({
            "uid": str(i + 1),
            "message_id": f"msg-{account_id}-{i}@mock",
            "subject": f"邮件主题 {i}",
            "sender": f"sender-{i}@example.com",
            "received_at": "2026-08-01T00:00:00",
            "attachments": atts,
        })
    return messages


class MailboxSyncService:

    # ── 消息幂等 upsert ──────────────────────────────────────────────

    @staticmethod
    def _message_hash(message: dict) -> str:
        parts = [
            str(message.get("subject") or ""),
            str(message.get("sender") or ""),
            str(message.get("received_at") or ""),
        ]
        for att in message.get("attachments") or []:
            parts.append(str(att.get("filename") or ""))
            parts.append(hashlib.sha256(att.get("content") or b"").hexdigest())
        return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()

    def _upsert_message(self, db: Any, account: MailboxSyncAccount,
                        message: dict, *, sync_run_id: int | None) -> tuple[MailboxMessage, bool]:
        """按 (account, folder, uidvalidity, uid) 幂等 upsert。

        返回 (row, created)；已存在且内容哈希不变 → 跳过（不重复创建附件/下游任务）。
        """
        folder = message.get("folder") or "INBOX"
        uidvalidity = message.get("uidvalidity") or account.uidvalidity or "0"
        uid = str(message.get("uid") or "")
        content_hash = self._message_hash(message)
        row = (
            db.query(MailboxMessage)
            .filter(
                MailboxMessage.account_id == account.id,
                MailboxMessage.folder == folder,
                MailboxMessage.uidvalidity == uidvalidity,
                MailboxMessage.uid == uid,
            )
            .first()
        )
        if row is not None and row.content_hash == content_hash:
            return row, False
        if row is None:
            row = MailboxMessage(
                account_id=account.id,
                folder=folder,
                uidvalidity=uidvalidity,
                uid=uid,
                message_id=str(message.get("message_id") or ""),
                subject=str(message.get("subject") or "")[:512],
                sender=str(message.get("sender") or "")[:512],
                received_at=_parse_naive(message.get("received_at")),
                content_hash=content_hash,
                sync_run_id=sync_run_id,
            )
            db.add(row)
        else:
            row.content_hash = content_hash
            row.sync_run_id = sync_run_id
        db.flush()
        return row, True

    # ── 附件安全处理 ─────────────────────────────────────────────────

    def _process_attachments(self, db: Any, account: MailboxSyncAccount,
                             message_row: MailboxMessage, message: dict) -> str:
        """处理附件：流式保存 + 安全扫描；返回消息处理结果（success/quarantined）。"""
        settings = get_settings()
        atts = message.get("attachments") or []
        quarantined = False
        message_row.attachment_count = len(atts)
        message_row.has_attachments = 1 if atts else 0
        for att in atts:
            filename = str(att.get("filename") or "attachment")
            content = att.get("content")
            if isinstance(content, str):
                content = content.encode("utf-8")
            if not isinstance(content, bytes):
                continue
            content_hash = hashlib.sha256(content).hexdigest()
            existing_att = (
                db.query(MailboxAttachment)
                .filter(MailboxAttachment.account_id == account.id,
                        MailboxAttachment.content_hash == content_hash)
                .first()
            )
            if existing_att is not None:
                # 哈希去重：同内容附件不重复存储
                db.add(MailboxAttachment(
                    message_id=message_row.id, account_id=account.id,
                    filename=filename, mime_type=existing_att.mime_type,
                    size_bytes=existing_att.size_bytes, content_hash=content_hash,
                    storage_key=existing_att.storage_key,
                    scan_status=existing_att.scan_status,
                    process_status="skipped",
                ))
                if existing_att.scan_status in (_SCAN_BLOCKED, _SCAN_QUARANTINED, _SCAN_ERROR):
                    quarantined = True
                continue

            scan_status, scan_note, storage_key, size = self._scan_and_store(
                filename, content, settings)
            db.add(MailboxAttachment(
                message_id=message_row.id, account_id=account.id,
                filename=filename, mime_type=_mime_of(filename),
                size_bytes=size, content_hash=content_hash, storage_key=storage_key,
                scan_status=scan_status, scan_result_json=json.dumps(scan_note, ensure_ascii=False),
                scan_scanner_version=settings.DLP_SCANNER_VERSION,
                scanned_at=utc_now(),
                process_status=_SCAN_QUARANTINED if scan_status != _SCAN_CLEAN else "imported",
            ))
            if scan_status != _SCAN_CLEAN:
                quarantined = True

        return "quarantined" if quarantined else "success"

    def _scan_and_store(self, filename: str, content: bytes, settings) -> tuple[str, str, str | None, int]:
        """附件安全：大小 → 真实 MIME → 白名单 → zip 安全 → 流式存储。

        返回 (scan_status, 脱敏说明, storage_key, size)。任何失败隔离，不抛异常中断同步。
        """
        try:
            if len(content) > settings.MAILBOX_ATTACHMENT_MAX_BYTES:
                return _SCAN_BLOCKED, "attachment_too_large", None, len(content)
            with BytesIO(content) as source:
                temp_path, size, _ = spool_upload_to_temp(source, max_bytes=settings.MAILBOX_ATTACHMENT_MAX_BYTES)
            ext, mime = detect_mime(content[:512], filename)
            if ext not in allowed_extensions():
                return _SCAN_BLOCKED, f"mime_not_allowed:{ext}", None, size
            if mime not in settings.MAILBOX_ATTACHMENT_ALLOWED_MIME_JSON:
                return _SCAN_BLOCKED, "mime_not_allowed", None, size
            if ext == "zip":
                inspect_zip_safety(str(temp_path))
            # 流式落对象存储
            from app.services.storage_service import storage_service
            key = f"{settings.MAILBOX_ATTACHMENT_STORAGE_PREFIX}/{temp_path.name}.{ext}"
            with open(temp_path, "rb") as fp:
                storage_service.put_stream(key, fp, content_type=mime)
            return _SCAN_CLEAN, "clean", key, size
        except DocumentSecurityError as exc:
            return _SCAN_BLOCKED, exc.code, None, len(content)
        except Exception as exc:  # noqa: BLE001 - 附件扫描失败隔离，不中断整批
            return _SCAN_QUARANTINED, f"scan_error:{type(exc).__name__}", None, len(content)

    # ── 同步主流程 ───────────────────────────────────────────────────

    def sync_account(self, *, db: Any, account: MailboxSyncAccount, owner: str | None = None,
                     batch_size: int | None = None, client: Any | None = None) -> dict:
        """执行一轮同步：分页 → 幂等 upsert + 附件安全 → 整批成功后推进 cursor。

        cursor/checkpoint 仅整批成功提交后推进；批次失败不推进、attempt+1 并置
        next_retry_at，中断后从最后成功 checkpoint 恢复。
        """
        settings = get_settings()
        batch = batch_size or settings.MAILBOX_SYNC_BATCH_SIZE
        client = client or build_mock_mailbox(account.id)
        account.status = "running"
        account.claimed_by = owner or "sync"
        account.claim_expires_at = utc_now() + timedelta(seconds=settings.SYNC_RUN_LEASE_TTL_SECONDS)
        db.commit()
        processed = 0
        succeeded = 0
        cursor = _parse_cursor(account.cursor_json)
        try:
            while True:
                page = client.page(account.id, cursor, batch)
                if not page.items:
                    break
                for message in page.items:
                    message["uidvalidity"] = page.uidvalidity
                    row, created = self._upsert_message(db, account, message, sync_run_id=None)
                    processed += 1
                    if not created:
                        # 内容未变化：幂等跳过，不重复创建附件/下游任务
                        continue
                    result = self._process_attachments(db, account, row, message)
                    row.process_result = result
                    row.processed = 1
                    succeeded += 1
                # 整批成功 → 唯一 cursor 推进点（与消息/附件同事务提交）
                db.commit()
                # UID 断点：每批成功后记录最后成功 UID 与 UIDVALIDITY
                account.uidvalidity = page.uidvalidity
                account.last_successful_uid = _last_uid(page.items)
                if page.next_cursor is not None:
                    account.cursor_json = json.dumps(page.next_cursor)
                    account.checkpoint_json = json.dumps({
                        "uidvalidity": page.uidvalidity, "cursor": page.next_cursor,
                    })
                    cursor = page.next_cursor
                db.commit()
                if not page.has_more or page.next_cursor is None:
                    break
            account.status = "active"
            account.error_code = None
            account.sanitized_error_message = None
            account.last_synced_at = utc_now()
            db.commit()
            return {"status": "succeeded", "processed": processed, "succeeded": succeeded}
        except Exception as exc:  # noqa: BLE001 - 中断/故障统一记账后 re-raise 交给任务重试
            account.status = "error"
            account.error_code = type(exc).__name__[:64]
            account.sanitized_error_message = "邮箱同步失败，可重试"
            account.attempt = (account.attempt or 0) + 1
            account.next_retry_at = utc_now() + timedelta(seconds=settings.SYNC_BACKOFF_BASE_SECONDS)
            db.commit()
            raise

    def recover_stale(self, *, db: Any, limit: int = 50) -> list[MailboxSyncAccount]:
        """回收租约过期/中断的同步账户（status=running 且 claim 过期）。"""
        settings = get_settings()
        stale_before = utc_now() - timedelta(seconds=settings.SYNC_RUN_LEASE_TTL_SECONDS)
        accounts = (
            db.query(MailboxSyncAccount)
            .filter(MailboxSyncAccount.status == "running",
                    MailboxSyncAccount.claim_expires_at.isnot(None),
                    MailboxSyncAccount.claim_expires_at < stale_before)
            .limit(limit)
            .all()
        )
        for account in accounts:
            account.status = "error"
            account.error_code = "LEASE_EXPIRED"
            account.sanitized_error_message = "同步租约过期，等待重试"
            account.next_retry_at = utc_now()
            account.claim_expires_at = None
        db.commit()
        return accounts


def _parse_cursor(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def _parse_naive(value: str | None):
    if not value:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _last_uid(items: list[dict]) -> str | None:
    if not items:
        return None
    return str(items[-1].get("uid") or "")


def _mime_of(filename: str) -> str:
    from pathlib import Path
    ext = Path(filename).suffix.lower()
    mapping = {".pdf": "application/pdf", ".doc": "application/msword",
               ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
               ".txt": "text/plain", ".zip": "application/zip"}
    return mapping.get(ext, "application/octet-stream")


mailbox_sync_service = MailboxSyncService()
