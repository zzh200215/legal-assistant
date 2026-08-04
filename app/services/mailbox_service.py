from __future__ import annotations

import base64
import email
import hashlib
import imaplib
import json
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.time import utc_now
from app.models.connector import ExternalConnector, MailboxMessage
from app.models.email import EmailDraft
from app.models.task import Task
from app.models.user import User
from app.services.oplog_service import oplog_service
from app.services.task_service import task_service


class MailboxService:
    CONNECTOR_TYPE = "imap_mailbox"
    _ACTION_PATTERN = re.compile(r"(请|需|需要|麻烦|跟进|确认|回复|提交|处理|安排|完成|\bconfirm\b|\breply\b|\bfollow up\b|\bsubmit\b|\bcomplete\b|\baction required\b)", re.IGNORECASE)
    _HIGH_PATTERN = re.compile(r"(紧急|urgent|立即|今天|截止|逾期|阻塞|风险)", re.IGNORECASE)
    _SUBSCRIPTION_PATTERN = re.compile(r"(unsubscribe|newsletter|订阅|退订|推广|促销)", re.IGNORECASE)

    @staticmethod
    def _fernet() -> Fernet:
        settings = get_settings()
        raw_key = settings.CONNECTOR_CREDENTIAL_ENCRYPTION_KEY.strip()
        if raw_key:
            try:
                return Fernet(raw_key.encode("utf-8"))
            except ValueError as exc:
                raise ValueError("CONNECTOR_CREDENTIAL_ENCRYPTION_KEY 不是有效的 Fernet 密钥") from exc
        derived = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest())
        return Fernet(derived)

    def encrypt_credentials(self, payload: dict) -> str:
        return self._fernet().encrypt(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("utf-8")

    def decrypt_credentials(self, ciphertext: str | None) -> dict:
        if not ciphertext:
            raise ValueError("邮箱连接器缺少凭据")
        try:
            payload = json.loads(self._fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8"))
        except (InvalidToken, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("邮箱连接器凭据不可用，请重新授权") from exc
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _decode_header(value: str | None) -> str:
        if not value:
            return ""
        chunks = []
        for part, encoding in decode_header(value):
            if isinstance(part, bytes):
                try:
                    chunks.append(part.decode(encoding or "utf-8", errors="replace"))
                except LookupError:
                    chunks.append(part.decode("utf-8", errors="replace"))
            else:
                chunks.append(part)
        return "".join(chunks).strip()

    @staticmethod
    def _body_text(message: email.message.Message) -> str:
        candidates = []
        parts = message.walk() if message.is_multipart() else [message]
        for part in parts:
            if part.get_content_maintype() == "multipart" or part.get_filename():
                continue
            if part.get_content_type() not in {"text/plain", "text/html"}:
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, errors="replace")
            except LookupError:
                text = payload.decode("utf-8", errors="replace")
            if part.get_content_type() == "text/html":
                text = re.sub(r"<[^>]+>", " ", text)
            candidates.append(text)
        return re.sub(r"\s+", " ", " ".join(candidates)).strip()[:12000]

    def _classify(self, *, sender: str, subject: str, body: str, important_senders: list[str]) -> tuple[str, str, str]:
        text = f"{subject} {body}"
        sender_lower = sender.lower()
        normalized_senders = [item.strip().lower() for item in important_senders if item.strip()]
        importance = "high" if self._HIGH_PATTERN.search(text) or any(item in sender_lower for item in normalized_senders) else "normal"
        if self._SUBSCRIPTION_PATTERN.search(text):
            category = "subscription"
        elif self._ACTION_PATTERN.search(text):
            category = "action"
        elif any(item in sender_lower for item in normalized_senders):
            category = "team"
        else:
            category = "other"
        compact = re.sub(r"\s+", " ", body).strip()
        summary = compact[:220] + ("..." if len(compact) > 220 else "")
        return category, importance, summary or "邮件正文为空或无法解析。"

    def priority_score(self, message: MailboxMessage) -> int:
        score = 0
        text = f"{message.subject or ''} {message.body_text or ''}"
        if message.importance == "high": score += 60
        if message.category == "action": score += 25
        if self._HIGH_PATTERN.search(text): score += 15
        if message.received_at:
            received_at = message.received_at.replace(tzinfo=None) if message.received_at.tzinfo else message.received_at
            if received_at >= utc_now() - timedelta(days=1): score += 10
        return score

    @staticmethod
    def _parse_received_at(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError, IndexError):
            return None

    def create_imap_connector(self, *, db: Session, user: User, request) -> ExternalConnector:
        config = {
            "host": request.host.strip(),
            "port": request.port,
            "mailbox": request.mailbox.strip() or "INBOX",
            "use_ssl": request.use_ssl,
            "max_messages": request.max_messages,
            "important_senders": [item.strip() for item in request.important_senders if item.strip()],
        }
        credentials = {"username": request.username.strip(), "password": request.password}
        connector = ExternalConnector(
            user_id=user.id,
            organization_id=user.organization_id,
            department_id=user.department_id,
            connector_type=self.CONNECTOR_TYPE,
            name=request.name.strip(),
            status="active",
            config_json=json.dumps(config, ensure_ascii=False),
            credential_ciphertext=self.encrypt_credentials(credentials),
            sync_cursor_json=json.dumps({}, ensure_ascii=False),
        )
        db.add(connector)
        db.commit()
        db.refresh(connector)
        oplog_service.log(
            module="mailbox",
            action="imap_connector_created",
            db=db,
            user_id=user.id,
            target_type="connector",
            target_id=connector.id,
            detail=f"connector_type={self.CONNECTOR_TYPE}",
        )
        return connector

    def sync_imap_connector(self, connector: ExternalConnector, *, db: Session) -> dict:
        if connector.connector_type != self.CONNECTOR_TYPE:
            raise ValueError("Unsupported mailbox connector")
        config = self._parse_json(connector.config_json)
        cursor = self._parse_json(connector.sync_cursor_json)
        credentials = self.decrypt_credentials(connector.credential_ciphertext)
        host = str(config.get("host") or "").strip()
        if not host:
            raise ValueError("邮箱服务器地址缺失")
        mailbox = str(config.get("mailbox") or "INBOX")
        port = int(config.get("port") or 993)
        max_messages = min(max(int(config.get("max_messages") or 50), 1), 200)
        client = imaplib.IMAP4_SSL(host, port) if config.get("use_ssl", True) else imaplib.IMAP4(host, port)
        imported = skipped = scanned = 0
        category_counts: dict[str, int] = {}
        try:
            status, _ = client.login(str(credentials.get("username") or ""), str(credentials.get("password") or ""))
            if status != "OK":
                raise ValueError("邮箱认证失败")
            status, _ = client.select(mailbox, readonly=True)
            if status != "OK":
                raise ValueError("无法读取指定邮箱文件夹")
            status, data = client.uid("search", None, "ALL")
            if status != "OK":
                raise ValueError("无法查询邮箱消息")
            raw_uids = (data[0] or b"").split() if data else []
            last_uid = int(cursor.get("last_uid") or 0)
            uids = [int(value) for value in raw_uids if value.isdigit() and int(value) > last_uid]
            uids = uids[-max_messages:]
            scanned = len(uids)
            important_senders = config.get("important_senders") if isinstance(config.get("important_senders"), list) else []
            for uid in uids:
                status, payload = client.uid("fetch", str(uid), "(RFC822)")
                if status != "OK" or not payload:
                    continue
                raw = next((item[1] for item in payload if isinstance(item, tuple) and len(item) > 1), None)
                if not raw:
                    continue
                message = email.message_from_bytes(raw)
                sender = self._decode_header(message.get("From"))
                recipient = self._decode_header(message.get("To"))
                subject = self._decode_header(message.get("Subject")) or "(无主题)"
                body = self._body_text(message)
                category, importance, summary = self._classify(
                    sender=sender,
                    subject=subject,
                    body=body,
                    important_senders=important_senders,
                )
                category_counts[category] = category_counts.get(category, 0) + 1
                existing = db.query(MailboxMessage).filter(
                    MailboxMessage.connector_id == connector.id,
                    MailboxMessage.message_uid == str(uid),
                ).first()
                if existing:
                    skipped += 1
                    continue
                db.add(
                    MailboxMessage(
                        connector_id=connector.id,
                        user_id=connector.user_id,
                        message_uid=str(uid),
                        mailbox=mailbox,
                        thread_id=self._decode_header(message.get("Message-ID")) or None,
                        sender=sender or None,
                        recipient=recipient or None,
                        subject=subject,
                        body_text=body,
                        summary=summary,
                        category=category,
                        importance=importance,
                        received_at=self._parse_received_at(message.get("Date")),
                    )
                )
                imported += 1
            if uids:
                cursor["last_uid"] = max(uids)
                cursor["mailbox"] = mailbox
                connector.sync_cursor_json = json.dumps(cursor, ensure_ascii=False)
            db.commit()
        finally:
            try:
                client.logout()
            except Exception:
                pass
        return {
            "source": f"imap://{host}/{mailbox}",
            "scanned_count": scanned,
            "imported_count": imported,
            "skipped_count": skipped,
            "category_counts": category_counts,
        }

    @staticmethod
    def _parse_json(value: str | None) -> dict:
        try:
            payload = json.loads(value or "{}")
            return payload if isinstance(payload, dict) else {}
        except (TypeError, ValueError):
            return {}

    def list_messages(
        self,
        *,
        db: Session,
        user: User,
        connector_id: int | None = None,
        category: str | None = None,
        importance: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[MailboxMessage], int]:
        query = db.query(MailboxMessage)
        if user.role != "admin":
            query = query.filter(MailboxMessage.user_id == user.id)
        if connector_id is not None:
            query = query.filter(MailboxMessage.connector_id == connector_id)
        if category:
            query = query.filter(MailboxMessage.category == category)
        if importance:
            query = query.filter(MailboxMessage.importance == importance)
        total = query.count()
        rows = query.all()
        rows.sort(key=lambda row: (self.priority_score(row), row.received_at or row.created_at, row.id), reverse=True)
        rows = rows[(page - 1) * page_size : page * page_size]
        return rows, total

    def top_important(self, *, db: Session, user: User, limit: int = 5) -> list[MailboxMessage]:
        since = utc_now() - timedelta(days=1)
        query = db.query(MailboxMessage).filter(MailboxMessage.received_at >= since)
        if user.role != "admin": query = query.filter(MailboxMessage.user_id == user.id)
        rows = query.all()
        rows.sort(key=lambda row: (self.priority_score(row), row.received_at or row.created_at, row.id), reverse=True)
        return rows[:limit]

    def reply_style_profile(self, *, db: Session, user: User) -> dict:
        rows = db.query(EmailDraft).filter(EmailDraft.user_id == user.id, EmailDraft.generation_type == "reply").order_by(EmailDraft.updated_at.desc()).limit(20).all()
        if not rows:
            return {"sample_count": 0, "tone": "professional", "instruction": "使用正式、简洁的商务表达。"}
        tones = [row.tone for row in rows if row.tone]
        tone = max(set(tones), key=tones.count) if tones else "professional"
        return {"sample_count": len(rows), "tone": tone, "instruction": f"参考该用户最近 {len(rows)} 封回复草稿的偏好，保持{tone}语气；不要复述或泄露历史邮件内容。"}

    def get_message(self, message_id: int, *, db: Session, user: User) -> MailboxMessage | None:
        query = db.query(MailboxMessage).filter(MailboxMessage.id == message_id)
        if user.role != "admin":
            query = query.filter(MailboxMessage.user_id == user.id)
        return query.first()

    def _retention_query(self, *, db: Session, user: User, retention_days: int, connector_id: int | None = None):
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        query = db.query(MailboxMessage).filter(
            MailboxMessage.user_id == user.id,
            MailboxMessage.task_id.is_(None),
        )
        if connector_id is not None:
            query = query.filter(MailboxMessage.connector_id == connector_id)
        # Older records may not have a parsed received timestamp; fall back to import time.
        query = query.filter(
            (MailboxMessage.received_at.is_not(None)) & (MailboxMessage.received_at < cutoff)
            | (MailboxMessage.received_at.is_(None)) & (MailboxMessage.created_at < cutoff)
        )
        return query, cutoff

    def retention_preview(self, *, db: Session, user: User, retention_days: int, connector_id: int | None = None) -> dict:
        query, cutoff = self._retention_query(
            db=db, user=user, retention_days=retention_days, connector_id=connector_id,
        )
        linked_query = db.query(MailboxMessage).filter(
            MailboxMessage.user_id == user.id,
            MailboxMessage.task_id.is_not(None),
        )
        if connector_id is not None:
            linked_query = linked_query.filter(MailboxMessage.connector_id == connector_id)
        linked_query = linked_query.filter(
            (MailboxMessage.received_at.is_not(None)) & (MailboxMessage.received_at < cutoff)
            | (MailboxMessage.received_at.is_(None)) & (MailboxMessage.created_at < cutoff)
        )
        return {
            "retention_days": retention_days,
            "connector_id": connector_id,
            "cutoff_at": cutoff,
            "purgeable_count": query.count(),
            "protected_task_linked_count": linked_query.count(),
        }

    def purge_retained_messages(self, *, db: Session, user: User, retention_days: int, connector_id: int | None = None) -> dict:
        preview = self.retention_preview(
            db=db, user=user, retention_days=retention_days, connector_id=connector_id,
        )
        query, _ = self._retention_query(
            db=db, user=user, retention_days=retention_days, connector_id=connector_id,
        )
        deleted_count = query.delete(synchronize_session=False)
        db.commit()
        oplog_service.log(
            module="mailbox",
            action="mailbox_retention_purged",
            db=db,
            user_id=user.id,
            target_type="mailbox_message",
            detail=(
                f"deleted={deleted_count}; retention_days={retention_days}; "
                f"connector_id={connector_id or 'all'}; protected_task_linked={preview['protected_task_linked_count']}"
            ),
        )
        return {**preview, "deleted_count": deleted_count}

    def task_suggestion(self, message_id: int, *, db: Session, user: User) -> dict:
        message = self.get_message(message_id, db=db, user=user)
        if not message:
            raise ValueError("Mailbox message not found")
        title = f"跟进邮件：{message.subject or '无主题'}"
        description = "\n".join(filter(None, [f"发件人：{message.sender}" if message.sender else None, message.summary]))
        return {
            "message_id": message.id,
            "title": title[:256],
            "description": description or None,
            "priority": "high" if message.importance == "high" else "medium",
            "already_created_task_id": message.task_id,
        }

    def confirm_task(self, message_id: int, *, db: Session, user: User, request) -> Task:
        message = self.get_message(message_id, db=db, user=user)
        if not message:
            raise ValueError("Mailbox message not found")
        if message.task_id:
            task = task_service.get(message.task_id, db, user_id=user.id, role=user.role, organization_id=user.organization_id, department_id=user.department_id)
            if task:
                return task
        suggestion = self.task_suggestion(message_id, db=db, user=user)
        task = task_service.create(
            title=(request.title or suggestion["title"]).strip()[:256],
            description=request.description if request.description is not None else suggestion["description"],
            assignee=request.assignee,
            priority=request.priority if request.priority in {"low", "medium", "high"} else suggestion["priority"],
            source_type="mailbox_email",
            source_id=message.id,
            user_id=user.id,
            db=db,
        )
        message.task_id = task.id
        db.commit()
        oplog_service.log(
            module="mailbox",
            action="mailbox_task_confirmed",
            db=db,
            user_id=user.id,
            target_type="mailbox_message",
            target_id=message.id,
            detail=f"task_id={task.id}",
        )
        return task


mailbox_service = MailboxService()
