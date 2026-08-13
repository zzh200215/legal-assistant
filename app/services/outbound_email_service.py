from __future__ import annotations

import base64
import hashlib
import json
import smtplib
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import make_msgid

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.external_resilience import external_resilience
from app.core.time import utc_now
from app.models.connector import ExternalConnector
from app.models.email import EmailAttachment, EmailDraft, EmailSendRequest, OutboundEmailPolicy
from app.models.user import User
from app.services.data_protection_service import data_protection_service
from app.services.dlp_scanner import dlp_scanner
from app.services.document_security import (
    allowed_extensions,
    build_virus_scanner,
    detect_mime,
    inspect_zip_safety,
    spool_upload_to_temp,
)
from app.services.oplog_service import oplog_service

# ── 邮件投递状态机（EmailSendRequest 作为邮件 Outbox）──────────────────────────
# pending(=requested) -> approved -> sending -> sent / failed / dead_letter
# 恢复：failed -> pending（重试/人工重新请求）/ dead_letter；dead_letter -> pending（人工）
EMAIL_REQ_PENDING = "pending"
EMAIL_REQ_APPROVED = "approved"
EMAIL_REQ_REJECTED = "rejected"
EMAIL_REQ_SENDING = "sending"
EMAIL_REQ_SENT = "sent"
EMAIL_REQ_FAILED = "failed"
EMAIL_REQ_BLOCKED = "blocked"
EMAIL_REQ_DEAD_LETTER = "dead_letter"

_EMAIL_REQ_TRANSITIONS: dict[str, frozenset] = {
    EMAIL_REQ_PENDING: frozenset({EMAIL_REQ_APPROVED, EMAIL_REQ_REJECTED, EMAIL_REQ_BLOCKED,
                                  EMAIL_REQ_FAILED, EMAIL_REQ_DEAD_LETTER}),
    EMAIL_REQ_APPROVED: frozenset({EMAIL_REQ_SENDING, EMAIL_REQ_REJECTED, EMAIL_REQ_BLOCKED}),
    EMAIL_REQ_SENDING: frozenset({EMAIL_REQ_SENT, EMAIL_REQ_FAILED, EMAIL_REQ_DEAD_LETTER}),
    EMAIL_REQ_FAILED: frozenset({EMAIL_REQ_PENDING, EMAIL_REQ_DEAD_LETTER, EMAIL_REQ_SENDING}),
    EMAIL_REQ_BLOCKED: frozenset({EMAIL_REQ_PENDING}),
    EMAIL_REQ_DEAD_LETTER: frozenset({EMAIL_REQ_PENDING}),
    EMAIL_REQ_SENT: frozenset(),
    EMAIL_REQ_REJECTED: frozenset(),
}


class EmailStateError(ValueError):
    """邮件投递状态机非法跳转。"""


def _fernet() -> Fernet:
    from app.core.config import get_settings

    settings = get_settings()
    raw_key = settings.CONNECTOR_CREDENTIAL_ENCRYPTION_KEY.strip()
    if raw_key:
        try:
            return Fernet(raw_key.encode("utf-8"))
        except ValueError as exc:
            raise ValueError("CONNECTOR_CREDENTIAL_ENCRYPTION_KEY 不是有效的 Fernet 密钥") from exc
    derived = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest())
    return Fernet(derived)


def _encrypt_credentials(payload: dict) -> str:
    return _fernet().encrypt(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("utf-8")


def _decrypt_credentials(ciphertext: str | None) -> dict:
    if not ciphertext:
        raise ValueError("连接器缺少凭据")
    try:
        payload = json.loads(_fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8"))
    except (InvalidToken, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("连接器凭据不可用，请重新授权") from exc
    return payload if isinstance(payload, dict) else {}


class OutboundEmailService:
    SMTP_CONNECTOR_TYPE = "smtp_outbound"

    @staticmethod
    def _json(value: str | None) -> dict:
        try:
            payload = json.loads(value or "{}")
            return payload if isinstance(payload, dict) else {}
        except (TypeError, ValueError):
            return {}

    @staticmethod
    def _domains(value: str | None) -> list[str]:
        try:
            data = json.loads(value or "[]")
            return [str(item).strip().lower().lstrip("@") for item in data if str(item).strip()]
        except (TypeError, ValueError):
            return []

    @staticmethod
    def _addresses(value: str | None) -> list[str]:
        if not value:
            return []
        return [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]

    @staticmethod
    def _draft_hash(draft: EmailDraft) -> str:
        source = "\n".join([draft.recipient or "", draft.cc or "", draft.subject or "", draft.content or ""])
        return hashlib.sha256(source.encode("utf-8")).hexdigest()

    @staticmethod
    def _smtp_send(config: dict, credentials: dict, message: EmailMessage) -> None:
        """同步发送一封邮件。由韧性层包裹：连接类错误可重试，写超时降级 AMBIGUOUS 不重试。"""
        with smtplib.SMTP(str(config.get("host")), int(config.get("port") or 587), timeout=20) as client:
            client.ehlo()
            if config.get("use_starttls", True):
                client.starttls()
                client.ehlo()
            client.login(str(credentials.get("username") or ""), str(credentials.get("password") or ""))
            client.send_message(message)

    # ── 状态机 ──────────────────────────────────────────────────────

    @classmethod
    def _transition(cls, request: EmailSendRequest, to: str, *, db: Session | None = None) -> None:
        """集中校验并执行 EmailSendRequest 状态迁移；禁止调用方任意改状态。"""
        current = request.status
        allowed = _EMAIL_REQ_TRANSITIONS.get(current, frozenset())
        if to not in allowed:
            raise EmailStateError(f"邮件状态机拒绝迁移: {current} -> {to}")
        request.status = to

    @classmethod
    def _mirror_notification(cls, db: Session, request: EmailSendRequest,
                             *, status: str, provider_message_id: str | None = None) -> None:
        """把邮件投递终态镜像回关联的通知事件（若存在）。"""
        if not request.notification_event_id:
            return
        from app.models.legal_notifications import LegalNotificationEvent

        event = db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.id == request.notification_event_id
        ).first()
        if not event:
            return
        from app.services.notification_service import notification_service

        if status == EMAIL_REQ_SENT:
            notification_service.mark_sent(db, event, provider_message_id=provider_message_id)
        elif status == EMAIL_REQ_APPROVED:
            notification_service.mark_approved(db, event)
        elif status == EMAIL_REQ_FAILED:
            notification_service.mark_failed(db, event, error_code="EMAIL_SEND_FAILED")
        elif status == EMAIL_REQ_DEAD_LETTER:
            notification_service.mark_dead_letter(db, event, reason="邮件投递重试耗尽或不可恢复错误",
                                                  error_code="EMAIL_DEAD_LETTER")

    def _policy(self, *, db: Session, organization_id: int | None) -> OutboundEmailPolicy | None:
        if organization_id is not None:
            policy = db.query(OutboundEmailPolicy).filter(OutboundEmailPolicy.organization_id == organization_id).first()
            if policy:
                return policy
        return db.query(OutboundEmailPolicy).filter(OutboundEmailPolicy.organization_id.is_(None)).first()

    def serialize_policy(self, policy: OutboundEmailPolicy | None, organization_id: int | None) -> dict:
        if not policy:
            return {"id": None, "organization_id": organization_id, "enabled": False, "allowed_recipient_domains": [], "max_sends_per_hour": 20, "require_approval": True, "dlp_enabled": True, "dlp_action": "block", "updated_at": None}
        return {"id": policy.id, "organization_id": policy.organization_id, "enabled": policy.enabled, "allowed_recipient_domains": self._domains(policy.allowed_recipient_domains_json), "max_sends_per_hour": policy.max_sends_per_hour, "require_approval": policy.require_approval, "dlp_enabled": bool(policy.dlp_enabled), "dlp_action": policy.dlp_action or "block", "updated_at": policy.updated_at}

    def update_policy(self, *, db: Session, user: User, request) -> OutboundEmailPolicy:
        policy = self._policy(db=db, organization_id=user.organization_id)
        if not policy:
            policy = OutboundEmailPolicy(organization_id=user.organization_id)
            db.add(policy)
        policy.enabled = request.enabled
        policy.allowed_recipient_domains_json = json.dumps(sorted({item.strip().lower().lstrip("@") for item in request.allowed_recipient_domains if item.strip()}), ensure_ascii=False)
        policy.max_sends_per_hour = request.max_sends_per_hour
        policy.require_approval = True  # P3 never permits automatic SMTP delivery.
        policy.dlp_enabled = bool(request.dlp_enabled)
        policy.dlp_action = request.dlp_action
        policy.updated_by_user_id = user.id
        db.commit()
        db.refresh(policy)
        oplog_service.log(module="outbound_email", action="outbound_policy_updated", db=db, user_id=user.id, target_type="outbound_policy", target_id=policy.id, detail=f"enabled={policy.enabled}; domains={len(self._domains(policy.allowed_recipient_domains_json))}")
        return policy

    def _scan_dlp(self, *, draft: EmailDraft, policy: OutboundEmailPolicy | None) -> dict:
        if not policy or not policy.dlp_enabled:
            return {"findings": [], "total_count": 0, "max_severity": None, "status": "disabled", "blocked": False}
        inspection = data_protection_service.inspect(f"主题：{draft.subject}\n正文：{draft.content}")
        blocked = data_protection_service.should_block(inspection, action=policy.dlp_action or "block")
        return {
            **inspection,
            "status": "blocked" if blocked else ("warning" if inspection["findings"] else "clean"),
            "blocked": blocked,
        }

    @staticmethod
    def _store_dlp_result(request: EmailSendRequest, result: dict) -> None:
        request.dlp_status = str(result.get("status") or "not_scanned")
        request.dlp_findings_json = json.dumps(result.get("findings") or [], ensure_ascii=False)
        request.dlp_scanned_at = datetime.now(timezone.utc)

    def create_smtp_connector(self, *, db: Session, user: User, request) -> ExternalConnector:
        config = {"host": request.host.strip(), "port": request.port, "from_address": request.from_address.strip(), "use_starttls": request.use_starttls}
        credentials = {"username": request.username.strip(), "password": request.password}
        connector = ExternalConnector(
            user_id=user.id, organization_id=user.organization_id, department_id=user.department_id,
            connector_type=self.SMTP_CONNECTOR_TYPE, name=request.name.strip(), status="active",
            config_json=json.dumps(config, ensure_ascii=False), credential_ciphertext=_encrypt_credentials(credentials),
        )
        db.add(connector)
        db.commit()
        db.refresh(connector)
        oplog_service.log(module="outbound_email", action="smtp_connector_created", db=db, user_id=user.id, target_type="connector", target_id=connector.id, detail="connector_type=smtp_outbound")
        return connector

    def list_smtp_connectors(self, *, db: Session, user: User) -> list[ExternalConnector]:
        return (
            db.query(ExternalConnector)
            .filter(
                ExternalConnector.user_id == user.id,
                ExternalConnector.connector_type == self.SMTP_CONNECTOR_TYPE,
            )
            .order_by(ExternalConnector.id.asc())
            .all()
        )

    def _validate_policy(self, *, db: Session, user: User, recipient: str, cc: str | None = None) -> OutboundEmailPolicy:
        policy = self._policy(db=db, organization_id=user.organization_id)
        if not policy or not policy.enabled:
            raise ValueError("组织外发邮件已停用")
        addresses = self._addresses(recipient) + self._addresses(cc)
        if not addresses:
            raise ValueError("草稿缺少收件人")
        if len(addresses) > 5:
            raise ValueError("单次发送最多允许 5 个收件人")
        domains = self._domains(policy.allowed_recipient_domains_json)
        if not domains:
            raise ValueError("组织尚未配置收件人白名单")
        invalid = [address for address in addresses if "@" not in address or address.rsplit("@", 1)[1].lower() not in domains]
        if invalid:
            raise ValueError("收件人不在组织白名单内")
        return policy

    def _get_owned_draft(self, draft_id: int, *, db: Session, user: User) -> EmailDraft:
        draft = db.query(EmailDraft).filter(EmailDraft.id == draft_id, EmailDraft.user_id == user.id).first()
        if not draft:
            raise ValueError("Draft not found")
        return draft

    def _get_smtp_connector(self, connector_id: int, *, db: Session, user: User) -> ExternalConnector:
        connector = db.query(ExternalConnector).filter(ExternalConnector.id == connector_id, ExternalConnector.user_id == user.id, ExternalConnector.connector_type == self.SMTP_CONNECTOR_TYPE).first()
        if not connector or connector.status != "active":
            raise ValueError("SMTP 连接器不可用")
        return connector

    def request_send(self, draft_id: int, *, connector_id: int, db: Session, user: User) -> EmailSendRequest:
        draft = self._get_owned_draft(draft_id, db=db, user=user)
        self._get_smtp_connector(connector_id, db=db, user=user)
        policy = self._validate_policy(db=db, user=user, recipient=draft.recipient or "", cc=draft.cc)
        fingerprint = self._draft_hash(draft)
        # 兼容旧行（idempotency_key 为随机 uuid）：按 draft_id + content_hash 去重
        existing = db.query(EmailSendRequest).filter(EmailSendRequest.draft_id == draft.id, EmailSendRequest.content_hash == fingerprint, EmailSendRequest.status.in_(["pending", "approved", "sending", "sent"])).first()
        if existing:
            return existing
        dlp_result = self._scan_dlp(draft=draft, policy=policy)
        # 确定性幂等键：同草稿+同内容复用同一行（跨请求去重），而非随机 uuid
        idem_key = None
        if get_settings().EMAIL_SEND_DETERMINISTIC_IDEMPOTENCY:
            idem_key = f"email:{draft.id}:{fingerprint[:16]}"
            by_key = db.query(EmailSendRequest).filter(EmailSendRequest.idempotency_key == idem_key).first()
            if by_key is not None:
                if by_key.status in ("pending", "approved", "sending", "sent"):
                    return by_key
                # failed/rejected：同一确定性键复用该行，重置为待审批（换连接器也允许）
                by_key.smtp_connector_id = connector_id
                by_key.status = "blocked" if dlp_result["blocked"] else "pending"
                by_key.rejection_note = None
                by_key.error_message = None
                by_key.sent_at = None
                by_key.provider_message_id = None
                by_key.approved_at = None
                by_key.approved_by_user_id = None
                self._store_dlp_result(by_key, dlp_result)
                db.commit()
                db.refresh(by_key)
                return by_key
        request = EmailSendRequest(
            draft_id=draft.id, smtp_connector_id=connector_id, user_id=user.id, organization_id=user.organization_id,
            recipient=draft.recipient or "", cc=draft.cc, subject=draft.subject, content_hash=fingerprint,
            idempotency_key=idem_key or uuid.uuid4().hex, status="blocked" if dlp_result["blocked"] else "pending",
            max_attempts=get_settings().EMAIL_DELIVERY_MAX_ATTEMPTS,
        )
        self._store_dlp_result(request, dlp_result)
        db.add(request)
        db.commit()
        db.refresh(request)
        action = "email_send_dlp_blocked" if dlp_result["blocked"] else "email_send_requested"
        oplog_service.log(module="outbound_email", action=action, db=db, user_id=user.id, target_type="email_send_request", target_id=request.id, detail=f"draft_id={draft.id}; connector_id={connector_id}; dlp={data_protection_service.audit_summary(dlp_result)}")
        return request

    # ── 通知邮件（真实发送：EmailSendRequest 即 Outbox）────────────────────────

    def create_notification_email(
        self,
        *,
        db: Session,
        user: User,
        notification_event,
        subject: str,
        body: str,
        recipient: str,
        auto_approve: bool,
    ) -> EmailSendRequest | None:
        """为通知事件创建 EmailDraft + EmailSendRequest（同事务）。

        返回请求；无可用 SMTP 连接器/策略禁止时返回 None（调用方决定降级路径）。
        """
        policy = self._policy(db=db, organization_id=user.organization_id)
        if not policy or not policy.enabled:
            return None
        connector = (
            db.query(ExternalConnector)
            .filter(
                ExternalConnector.user_id == user.id,
                ExternalConnector.connector_type == self.SMTP_CONNECTOR_TYPE,
                ExternalConnector.status == "active",
            )
            .order_by(ExternalConnector.id.asc())
            .first()
        )
        if not connector:
            return None

        draft = EmailDraft(
            user_id=user.id,
            organization_id=user.organization_id,
            subject=subject,
            recipient=recipient,
            content=body,
            purpose="系统通知",
            status="draft",
            generation_type="notification_email",
        )
        db.add(draft)
        db.flush()

        # 与 _perform_send 校验一致：content_hash 基于 draft 实际字段（含 cc）
        fingerprint = self._draft_hash(draft)
        idem_key = f"notify-email:{notification_event.id}:{fingerprint[:16]}"
        existing = db.query(EmailSendRequest).filter(
            EmailSendRequest.idempotency_key == idem_key
        ).first()
        if existing is not None:
            return existing

        request = EmailSendRequest(
            draft_id=draft.id,
            smtp_connector_id=connector.id,
            user_id=user.id,
            organization_id=user.organization_id,
            recipient=recipient,
            subject=subject,
            content_hash=fingerprint,
            idempotency_key=idem_key,
            status=EMAIL_REQ_APPROVED if auto_approve else EMAIL_REQ_PENDING,
            notification_event_id=notification_event.id,
            max_attempts=get_settings().EMAIL_DELIVERY_MAX_ATTEMPTS,
        )
        self._store_dlp_result(request, self._scan_dlp(draft=draft, policy=policy))
        db.add(request)
        db.flush()
        notification_event.email_send_request_id = request.id
        return request

    # ── 投递核心（用户 execute 与 worker 共用）────────────────────────────────

    def _attachments_for(self, db: Session, request: EmailSendRequest) -> list[EmailAttachment]:
        rows = db.query(EmailAttachment).filter(
            EmailAttachment.send_request_id == request.id
        ).order_by(EmailAttachment.id.asc()).all()
        if not rows:
            rows = db.query(EmailAttachment).filter(
                EmailAttachment.draft_id == request.draft_id
            ).order_by(EmailAttachment.id.asc()).all()
        return rows

    @staticmethod
    def _attachment_dlp_payloads(filename: str, mime: str | None, temp_path=None) -> list[str | None]:
        """构造附件 DLP 载荷：文件名 + MIME + 文本类内容的安全摘要（≤4KB，不落日志）。"""
        payloads: list[str | None] = [filename, mime or ""]
        if temp_path is not None:
            try:
                with open(temp_path, "rb") as fp:
                    head = fp.read(4096)
                if b"\x00" not in head:  # 文本类文件才纳入内容摘要扫描
                    payloads.append(head.decode("utf-8", errors="ignore"))
            except OSError:
                pass
        return payloads

    def _scan_attachments(self, db: Session, request: EmailSendRequest) -> bool:
        """扫描请求关联附件；任一附件 blocked/quarantined 返回 True（阻断发送）。"""
        blocked = False
        for att in self._attachments_for(db, request):
            if att.scan_status in ("blocked", "quarantined", "error"):
                blocked = True
                continue
            result = dlp_scanner.scan(payloads=self._attachment_dlp_payloads(att.filename, att.mime_type),
                                      action="block")
            att.scan_result_json = json.dumps(result.masked_summary, ensure_ascii=False)
            att.scan_scanner_version = result.scanner_version
            att.scanned_at = utc_now()
            att.scan_status = "blocked" if result.blocked else "clean"
            if result.blocked:
                blocked = True
        return blocked

    def upload_attachment(self, *, db: Session, user: User, draft_id: int, file) -> EmailAttachment:
        """上传邮件草稿附件：流式安全处理（大小/真实 MIME/白名单/zip-bomb/病毒扫描/哈希去重）+
        DLP 硬门禁。blocked 附件直接拒绝，不进入发送链路。"""
        settings = get_settings()
        draft = self._get_owned_draft(draft_id, db=db, user=user)
        filename = getattr(file, "filename", None) or "attachment"
        head = file.file.read(512)
        file.file.seek(0)

        # 1) 真实 MIME：magic-byte 嗅探 + 扩展名交叉校验；2) 白名单
        ext, mime = detect_mime(head, filename)
        if ext not in allowed_extensions():
            raise ValueError("附件类型不被允许")
        if mime not in settings.MAILBOX_ATTACHMENT_ALLOWED_MIME_JSON:
            raise ValueError("附件 MIME 不在允许白名单内")

        # 3) 流式落盘临时文件（边算 SHA-256，强制大小上限，禁止整体 read()）
        temp_path, size, content_hash = spool_upload_to_temp(
            file.file, max_bytes=settings.MAILBOX_ATTACHMENT_MAX_BYTES)
        try:
            if ext == "zip":
                inspect_zip_safety(str(temp_path))
            # 4) 病毒扫描（复用现有扫描器；未配置扫描器不伪造通过）
            scan_result = build_virus_scanner().scan(temp_path)
            if not scan_result.clean:
                raise ValueError("附件病毒扫描未通过")

            # 5) DLP 硬门禁：文件名 + MIME + 文本内容摘要
            result = dlp_scanner.scan(
                payloads=self._attachment_dlp_payloads(filename, mime, temp_path), action="block")
            if result.blocked:
                raise ValueError("附件命中高风险 DLP 策略")

            # 6) 哈希去重 + 流式落对象存储
            key = f"{settings.MAILBOX_ATTACHMENT_STORAGE_PREFIX}/email/{content_hash}.{ext}"
            from app.services.storage_service import storage_service

            with open(temp_path, "rb") as fp:
                storage_service.put_stream(key, fp, content_type=mime)

            row = EmailAttachment(
                draft_id=draft.id,
                organization_id=user.organization_id,
                filename=filename,
                mime_type=mime,
                size_bytes=size,
                content_hash=content_hash,
                storage_key=key,
                scan_status="clean",
                scan_result_json=json.dumps(result.masked_summary, ensure_ascii=False),
                scan_scanner_version=settings.DLP_SCANNER_VERSION,
                scanned_at=utc_now(),
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            oplog_service.log(module="outbound_email", action="email_attachment_uploaded", db=db,
                              user_id=user.id, target_type="email_draft", target_id=draft.id,
                              detail=f"attachment_id={row.id}; name={filename}; dlp={result.masked_summary}")
            return row
        finally:
            import os
            if os.path.exists(str(temp_path)):
                os.remove(str(temp_path))

    def _perform_send(
        self,
        *,
        db: Session,
        request: EmailSendRequest,
        user: User | None = None,
        owner: str | None = None,
    ) -> EmailSendRequest:
        """执行 SMTP 投递（用户触发或 worker 触发均走此路径，幂等、写超时不盲目重试）。"""
        settings = get_settings()
        user = user or db.query(User).filter(User.id == request.user_id).first()
        if user is None:
            raise ValueError("Send request user not found")
        draft = db.query(EmailDraft).filter(EmailDraft.id == request.draft_id).first()
        if not draft:
            raise ValueError("Draft not found")
        if self._draft_hash(draft) != request.content_hash:
            request.status = "rejected"
            request.rejection_note = "草稿内容已变更，需要重新申请发送"
            db.commit()
            raise ValueError("草稿内容已变更，需要重新申请发送")
        self._validate_policy(db=db, user=user, recipient=request.recipient, cc=request.cc)
        connector = self._get_smtp_connector(request.smtp_connector_id, db=db, user=user)
        policy = self._policy(db=db, organization_id=user.organization_id)

        # DLP 门禁（含附件）：block → 阻断，不进入 sending
        dlp_result = self._scan_dlp(draft=draft, policy=policy)
        self._store_dlp_result(request, dlp_result)
        if dlp_result["blocked"] or self._scan_attachments(db, request):
            request.status = EMAIL_REQ_BLOCKED
            request.rejection_note = "外发内容命中高风险 DLP 策略，已阻断"
            db.commit()
            oplog_service.log(module="outbound_email", action="email_send_dlp_blocked", db=db,
                              user_id=user.id, target_type="email_send_request", target_id=request.id,
                              detail=f"draft_id={draft.id}; phase=execute; dlp={data_protection_service.audit_summary(dlp_result)}")
            raise ValueError("外发内容命中高风险 DLP 策略，已阻断")

        since = datetime.now(timezone.utc) - timedelta(hours=1)
        sent_count = db.query(EmailSendRequest).filter(EmailSendRequest.user_id == user.id, EmailSendRequest.status == "sent", EmailSendRequest.sent_at >= since).count()
        if sent_count >= (policy.max_sends_per_hour if policy else 20):
            raise ValueError("已达到每小时发送上限")

        if request.status != EMAIL_REQ_SENDING:
            self._transition(request, EMAIL_REQ_SENDING)
        request.claimed_by = owner or f"manual:{user.id}"
        request.claim_expires_at = utc_now() + timedelta(seconds=settings.EMAIL_DELIVERY_CLAIM_TTL_SECONDS)
        config = self._json(connector.config_json)
        credentials = _decrypt_credentials(connector.credential_ciphertext)
        message_id = make_msgid(domain=str(config.get("from_address") or "").split("@")[-1] or None)
        message = EmailMessage()
        message["From"] = str(config.get("from_address") or credentials.get("username"))
        message["To"] = request.recipient
        if request.cc:
            message["Cc"] = request.cc
        if request.bcc:
            message["Bcc"] = request.bcc
        message["Subject"] = request.subject
        message["Message-ID"] = message_id
        message.set_content(draft.content)
        for att in self._attachments_for(db, request):
            content = _read_storage_bytes(att)
            if content:
                message.add_attachment(
                    content, maintype=(att.mime_type or "application/octet-stream").split("/")[0],
                    subtype=(att.mime_type or "application/octet-stream").split("/")[-1],
                    filename=att.filename,
                )
        # 发送前持久化 provider_message_id 作幂等确认：即使后续超时/中断，
        # 也能据此确认该邮件是否已交到 SMTP 服务（不盲目重试）。
        request.provider_message_id = message_id
        db.commit()
        try:
            external_resilience.call(
                lambda: self._smtp_send(config, credentials, message),
                service="smtp",
                op="send_message",
                connector_id=connector.id,
                method="POST",
            )
            request.status = EMAIL_REQ_SENT
            request.sent_at = datetime.now(timezone.utc)
            request.error_message = None
            request.error_code = None
            request.claim_expires_at = None
            # Billing drafts remain drafts until this successful provider handoff.
            metadata = self._json(draft.metadata_json)
            invoice_id = metadata.get("billing_invoice_id")
            if invoice_id:
                from app.models.legal_billing import LegalInvoice
                invoice = db.query(LegalInvoice).filter(LegalInvoice.id == invoice_id).first()
                if invoice and invoice.organization_id == request.organization_id and invoice.status == "draft":
                    invoice.status = "sent"
                    invoice.sent_at = request.sent_at
            db.commit()
            db.refresh(request)
            self._mirror_notification(db, request, status=EMAIL_REQ_SENT, provider_message_id=message_id)
            db.commit()
            oplog_service.log(module="outbound_email", action="email_sent", db=db, user_id=user.id,
                              target_type="email_send_request", target_id=request.id,
                              detail=f"draft_id={draft.id}; provider_message_id={message_id}")
            return request
        except Exception as exc:
            self._record_failure(db, request, exc)
            db.commit()
            self._mirror_notification(db, request, status=request.status)
            db.commit()
            oplog_service.log(module="outbound_email", action="email_send_failed", db=db, user_id=user.id,
                              target_type="email_send_request", target_id=request.id,
                              detail=f"draft_id={draft.id}; error=redacted")
            raise

    def _record_failure(self, db: Session, request: EmailSendRequest, exc: Exception) -> None:
        """错误分类记账：可重试退避，不可重试/超限进 dead letter。不吞异常。"""
        settings = get_settings()
        request.attempt = (request.attempt or 0) + 1
        from app.core.external_resilience import classify_exception, ExternalError

        if isinstance(exc, ExternalError):
            kind = exc.kind.value
            retryable = exc.retryable
            retry_after = exc.retry_after_seconds
        else:
            kind = type(exc).__name__
            retryable = classify_exception(exc) in ("network", "timeout", "connection", "rate_limited", "server_5xx")
            retry_after = None

        request.error_code = kind[:64]
        request.sanitized_error_message = "SMTP 发送失败，请检查连接器与系统日志"
        max_attempts = request.max_attempts or settings.EMAIL_DELIVERY_MAX_ATTEMPTS
        if retryable and request.attempt < max_attempts:
            from app.core.external_resilience import compute_backoff_delay
            delay = compute_backoff_delay(
                request.attempt,
                base_seconds=settings.EXTERNAL_BACKOFF_BASE_SECONDS,
                jitter=settings.EXTERNAL_BACKOFF_JITTER,
                max_wait_seconds=settings.EXTERNAL_MAX_WAIT_SECONDS,
                retry_after_seconds=retry_after,
            )
            self._transition(request, EMAIL_REQ_FAILED)
            request.next_retry_at = utc_now() + timedelta(seconds=delay)
        else:
            self._transition(request, EMAIL_REQ_DEAD_LETTER)
            request.dead_letter_at = utc_now()
            request.dead_letter_reason = "达到最大重试次数或不可恢复错误"

    # ── 用户触发执行（兼容旧 API）──────────────────────────────────────────────

    def execute_request(self, request_id: int, *, db: Session, user: User) -> EmailSendRequest:
        request = db.query(EmailSendRequest).filter(EmailSendRequest.id == request_id, EmailSendRequest.user_id == user.id).first()
        if not request:
            raise ValueError("Send request not found")
        if request.status == "sent":
            return request
        if request.status != "approved":
            raise ValueError("Send request must be approved")
        return self._perform_send(db=db, request=request, user=user)

    # ── Worker 领取投递（邮件 Outbox）──────────────────────────────────────────

    def claim_and_deliver(self, *, db: Session, request_id: int, owner: str) -> EmailSendRequest | None:
        """原子领取一条 approved/failed 可重试的投递请求并执行。

        返回 None 表示未领取到（被他人领取/状态不满足）。并发由条件 UPDATE 保证。
        """
        from sqlalchemy import text as sa_text

        now = utc_now()
        settings = get_settings()
        ttl = settings.EMAIL_DELIVERY_CLAIM_TTL_SECONDS
        stmt = sa_text(
            "UPDATE email_send_requests SET status=:sending, claimed_by=:owner, claim_expires_at=:exp "
            "WHERE id=:rid AND status IN (:a1, :a2) "
            "AND (next_retry_at IS NULL OR next_retry_at <= :now) "
            "AND (claim_expires_at IS NULL OR claim_expires_at < :now)"
        )
        result = db.execute(stmt, {
            "sending": EMAIL_REQ_SENDING, "owner": owner, "exp": now + timedelta(seconds=ttl),
            "rid": request_id, "a1": EMAIL_REQ_APPROVED, "a2": EMAIL_REQ_FAILED, "now": now,
        })
        if result.rowcount == 0:
            return None
        db.commit()
        request = db.query(EmailSendRequest).filter(EmailSendRequest.id == request_id).first()
        if not request:
            return None
        try:
            self._perform_send(db=db, request=request, owner=owner)
        except Exception:
            db.rollback()
            raise
        return request

    def claim_pending_batch(self, *, db: Session, owner: str, batch_size: int | None = None) -> list[EmailSendRequest]:
        """keyset 原子领取一批 approved/failed 且到期可投递的请求（并发安全）。"""
        from sqlalchemy import text as sa_text

        now = utc_now()
        settings = get_settings()
        ttl = settings.EMAIL_DELIVERY_CLAIM_TTL_SECONDS
        batch = batch_size or settings.NOTIFICATION_CLAIM_BATCH_SIZE
        stmt = sa_text(
            "UPDATE email_send_requests SET status=:sending, claimed_by=:owner, claim_expires_at=:exp "
            "WHERE id IN ("
            "  SELECT id FROM email_send_requests "
            "  WHERE status IN (:a1, :a2) "
            "  AND (next_retry_at IS NULL OR next_retry_at <= :now) "
            "  AND (claim_expires_at IS NULL OR claim_expires_at < :now) "
            "  ORDER BY id LIMIT :batch"
            ")"
        )
        db.execute(stmt, {
            "sending": EMAIL_REQ_SENDING, "owner": owner, "exp": now + timedelta(seconds=ttl),
            "a1": EMAIL_REQ_APPROVED, "a2": EMAIL_REQ_FAILED, "now": now, "batch": batch,
        })
        db.commit()
        return (
            db.query(EmailSendRequest)
            .filter(EmailSendRequest.claimed_by == owner, EmailSendRequest.status == EMAIL_REQ_SENDING)
            .order_by(EmailSendRequest.id.asc())
            .all()
        )

    def reclaim_stale(self, *, db: Session, now: datetime | None = None) -> int:
        """回收租约过期的 sending 请求（worker 崩溃后安全重领）。"""
        from datetime import timedelta as _td
        from sqlalchemy import text as sa_text

        now = now or utc_now()
        settings = get_settings()
        stale_before = now - _td(seconds=settings.EMAIL_DELIVERY_CLAIM_TTL_SECONDS)
        rows = (
            db.query(EmailSendRequest.id)
            .filter(
                EmailSendRequest.status == EMAIL_REQ_SENDING,
                EmailSendRequest.claim_expires_at.isnot(None),
                EmailSendRequest.claim_expires_at < stale_before,
            )
            .limit(200)
            .all()
        )
        for (rid,) in rows:
            db.execute(sa_text(
                "UPDATE email_send_requests SET status=:failed, claimed_by=NULL, claim_expires_at=NULL, "
                "error_code='LEASE_EXPIRED', sanitized_error_message='投递租约过期，等待重试' WHERE id=:rid"
            ), {"failed": EMAIL_REQ_FAILED, "rid": rid})
        db.commit()
        return len(rows)

    # ── 审批 / 列表 / 序列化（兼容旧 API）──────────────────────────────────────

    @staticmethod
    def _same_organization(request: EmailSendRequest, user: User) -> bool:
        return request.organization_id == user.organization_id

    def serialize_request(self, request: EmailSendRequest, *, db: Session, viewer: User) -> dict:
        requester = db.query(User).filter(User.id == request.user_id).first()
        approver = (
            db.query(User).filter(User.id == request.approved_by_user_id).first()
            if request.approved_by_user_id else None
        )
        can_decide = (
            viewer.role == "admin"
            and request.status == "pending"
            and request.user_id != viewer.id
            and self._same_organization(request, viewer)
        )
        return {
            "id": request.id,
            "draft_id": request.draft_id,
            "smtp_connector_id": request.smtp_connector_id,
            "user_id": request.user_id,
            "requester_username": requester.username if requester else None,
            "recipient": request.recipient,
            "cc": request.cc,
            "subject": request.subject,
            "status": request.status,
            "approved_at": request.approved_at,
            "approved_by_user_id": request.approved_by_user_id,
            "approver_username": approver.username if approver else None,
            "rejection_note": request.rejection_note,
            "sent_at": request.sent_at,
            "provider_message_id": request.provider_message_id,
            "error_message": request.error_message,
            "dlp_status": request.dlp_status,
            "dlp_findings": self._findings(request.dlp_findings_json),
            "dlp_scanned_at": request.dlp_scanned_at,
            "attempt": request.attempt,
            "max_attempts": request.max_attempts,
            "next_retry_at": request.next_retry_at,
            "dead_letter_at": request.dead_letter_at,
            "dead_letter_reason": request.dead_letter_reason,
            "created_at": request.created_at,
            "updated_at": request.updated_at,
            "can_decide": can_decide,
            "can_execute": request.status == "approved" and request.user_id == viewer.id,
        }

    @staticmethod
    def _findings(value: str | None) -> list[dict]:
        try:
            parsed = json.loads(value or "[]")
            return parsed if isinstance(parsed, list) else []
        except (TypeError, ValueError):
            return []

    def list_requests(self, *, db: Session, user: User, draft_id: int | None = None) -> list[EmailSendRequest]:
        query = db.query(EmailSendRequest)
        if user.role == "admin":
            if user.organization_id is None:
                query = query.filter(EmailSendRequest.organization_id.is_(None))
            else:
                query = query.filter(EmailSendRequest.organization_id == user.organization_id)
        else:
            query = query.filter(EmailSendRequest.user_id == user.id)
        if draft_id is not None:
            query = query.filter(EmailSendRequest.draft_id == draft_id)
        return query.order_by(EmailSendRequest.created_at.desc(), EmailSendRequest.id.desc()).all()

    def decide_request(self, request_id: int, *, approved: bool, note: str | None, db: Session, user: User) -> EmailSendRequest:
        if user.role != "admin":
            raise ValueError("Only organization administrators can decide send requests")
        request = db.query(EmailSendRequest).filter(EmailSendRequest.id == request_id).first()
        if not request:
            raise ValueError("Send request not found")
        if not self._same_organization(request, user):
            raise ValueError("Send request is outside your organization")
        if request.user_id == user.id:
            raise ValueError("Requesters cannot decide their own send requests")
        if request.status != "pending":
            raise ValueError("Send request already decided")
        self._transition(request, EMAIL_REQ_APPROVED if approved else EMAIL_REQ_REJECTED)
        request.approved_at = datetime.now(timezone.utc) if approved else None
        request.approved_by_user_id = user.id if approved else None
        request.rejection_note = (note or "").strip() or None
        db.commit()
        db.refresh(request)
        # 镜像：审批通过 → 关联通知事件可进入投递队列
        if approved and request.notification_event_id:
            self._mirror_notification(db, request, status=EMAIL_REQ_APPROVED)
            db.commit()
        oplog_service.log(module="outbound_email", action="email_send_approved" if approved else "email_send_rejected", db=db, user_id=user.id, target_type="email_send_request", target_id=request.id, detail=f"draft_id={request.draft_id}")
        return request

    # ── 死信与人工重试 ─────────────────────────────────────────────────────────

    def list_dead_letter(self, *, db: Session, user: User, limit: int = 50) -> list[EmailSendRequest]:
        query = db.query(EmailSendRequest).filter(EmailSendRequest.status == EMAIL_REQ_DEAD_LETTER)
        if user.role == "admin":
            if user.organization_id is None:
                query = query.filter(EmailSendRequest.organization_id.is_(None))
            else:
                query = query.filter(EmailSendRequest.organization_id == user.organization_id)
        else:
            query = query.filter(EmailSendRequest.user_id == user.id)
        return query.order_by(EmailSendRequest.dead_letter_at.desc(), EmailSendRequest.id.desc()).limit(limit).all()

    def _reset_dead_letter(self, request: EmailSendRequest, *, db: Session) -> None:
        """死信 → pending 重置（人工重试核心；权限由调用方保证）。"""
        self._transition(request, EMAIL_REQ_PENDING)
        request.attempt = 0
        request.next_retry_at = None
        request.dead_letter_at = None
        request.dead_letter_reason = None
        request.error_code = None
        request.sanitized_error_message = None

    def manual_retry(self, request_id: int, *, db: Session, user: User) -> EmailSendRequest:
        """人工重试死信：校验权限（本人或同组织 admin），保留原幂等键，完整审计。"""
        request = db.query(EmailSendRequest).filter(EmailSendRequest.id == request_id).first()
        if not request:
            raise ValueError("Send request not found")
        if not self._same_organization(request, user):
            raise ValueError("Send request is outside your organization")
        if request.user_id != user.id and user.role != "admin":
            raise ValueError("无权重试该投递")
        if request.status != EMAIL_REQ_DEAD_LETTER:
            raise ValueError("只有死信状态可人工重试")
        self._reset_dead_letter(request, db=db)
        db.commit()
        db.refresh(request)
        oplog_service.log(module="outbound_email", action="email_send_manual_retry", db=db,
                          user_id=user.id, target_type="email_send_request", target_id=request.id,
                          detail=f"draft_id={request.draft_id}; idempotency_key={request.idempotency_key}")
        return request

    # ── 事务性 OTP（免审批，走 DLP + 白名单）───────────────────────────────────

    def send_portal_otp(self, *, db: Session, user: User, recipient: str, otp: str) -> None:
        """Deliver a short-lived portal OTP through the organization's configured SMTP connector.

        This is a transactional authentication message, so it is not held for the normal
        human approval queue. Recipient-domain and DLP policy checks still apply.
        """
        policy = self._validate_policy(db=db, user=user, recipient=recipient)
        connector = db.query(ExternalConnector).filter(
            ExternalConnector.user_id == user.id,
            ExternalConnector.connector_type == self.SMTP_CONNECTOR_TYPE,
            ExternalConnector.status == "active",
        ).order_by(ExternalConnector.id.asc()).first()
        if not connector:
            raise ValueError("未配置可用的 SMTP 连接器")

        subject = "客户门户验证码"
        content = f"您的客户门户验证码为：{otp}\n验证码 5 分钟内有效，请勿向他人透露。"
        draft = EmailDraft(
            user_id=user.id,
            organization_id=user.organization_id,
            subject=subject,
            recipient=recipient,
            content=content,
            purpose="客户门户验证码",
            generation_type="portal_otp",
            status="sent",
        )
        dlp_result = self._scan_dlp(draft=draft, policy=policy)
        if dlp_result["blocked"]:
            raise ValueError("验证码邮件命中外发 DLP 策略")

        config = self._json(connector.config_json)
        credentials = _decrypt_credentials(connector.credential_ciphertext)
        message = EmailMessage()
        message["From"] = str(config.get("from_address") or credentials.get("username"))
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(content)
        external_resilience.call(
            lambda: self._smtp_send(config, credentials, message),
            service="smtp",
            op="send_portal_otp",
            connector_id=connector.id,
            method="POST",
        )
        db.add(draft)
        db.commit()
        oplog_service.log(module="portal", action="portal_otp_sent", db=db, user_id=user.id,
                          target_type="email_draft", target_id=draft.id, detail="transactional_otp=true")


def _read_storage_bytes(att: EmailAttachment) -> bytes | None:
    """读取附件对象存储内容；不可用返回 None（不阻塞主流程但跳过该附件）。"""
    if not att.storage_key:
        return None
    try:
        from app.services.storage_service import storage_service

        with storage_service.get_stream(att.storage_key) as stream:
            return stream.read()
    except Exception:  # noqa: BLE001 - 附件读取失败只跳过该附件
        return None


outbound_email_service = OutboundEmailService()
