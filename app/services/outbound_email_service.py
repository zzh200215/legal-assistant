from __future__ import annotations

import hashlib
import json
import smtplib
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import make_msgid

from sqlalchemy.orm import Session

from app.models.connector import ExternalConnector
from app.models.email import EmailDraft, EmailSendRequest, OutboundEmailPolicy
from app.models.user import User
from app.services.connector_service import connector_service
from app.services.data_protection_service import data_protection_service
from app.services.mailbox_service import mailbox_service
from app.services.oplog_service import oplog_service


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
        db.commit(); db.refresh(policy)
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
            config_json=json.dumps(config, ensure_ascii=False), credential_ciphertext=mailbox_service.encrypt_credentials(credentials),
        )
        db.add(connector); db.commit(); db.refresh(connector)
        oplog_service.log(module="outbound_email", action="smtp_connector_created", db=db, user_id=user.id, target_type="connector", target_id=connector.id, detail="connector_type=smtp_outbound")
        return connector

    def list_smtp_connectors(self, *, db: Session, user: User) -> list[ExternalConnector]:
        return [item for item in connector_service.list_connectors(db=db, user=user) if item.connector_type == self.SMTP_CONNECTOR_TYPE]

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
        existing = db.query(EmailSendRequest).filter(EmailSendRequest.draft_id == draft.id, EmailSendRequest.content_hash == fingerprint, EmailSendRequest.status.in_(["pending", "approved", "sending", "sent"])).first()
        if existing:
            return existing
        dlp_result = self._scan_dlp(draft=draft, policy=policy)
        request = EmailSendRequest(
            draft_id=draft.id, smtp_connector_id=connector_id, user_id=user.id, organization_id=user.organization_id,
            recipient=draft.recipient or "", cc=draft.cc, subject=draft.subject, content_hash=fingerprint,
            idempotency_key=uuid.uuid4().hex, status="blocked" if dlp_result["blocked"] else "pending",
        )
        self._store_dlp_result(request, dlp_result)
        db.add(request); db.commit(); db.refresh(request)
        action = "email_send_dlp_blocked" if dlp_result["blocked"] else "email_send_requested"
        oplog_service.log(module="outbound_email", action=action, db=db, user_id=user.id, target_type="email_send_request", target_id=request.id, detail=f"draft_id={draft.id}; connector_id={connector_id}; dlp={data_protection_service.audit_summary(dlp_result)}")
        return request

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
        request.status = "approved" if approved else "rejected"
        request.approved_at = datetime.now(timezone.utc) if approved else None
        request.approved_by_user_id = user.id if approved else None
        request.rejection_note = (note or "").strip() or None
        db.commit(); db.refresh(request)
        oplog_service.log(module="outbound_email", action="email_send_approved" if approved else "email_send_rejected", db=db, user_id=user.id, target_type="email_send_request", target_id=request.id, detail=f"draft_id={request.draft_id}")
        return request

    def execute_request(self, request_id: int, *, db: Session, user: User) -> EmailSendRequest:
        request = db.query(EmailSendRequest).filter(EmailSendRequest.id == request_id, EmailSendRequest.user_id == user.id).first()
        if not request:
            raise ValueError("Send request not found")
        if request.status == "sent":
            return request
        if request.status != "approved":
            raise ValueError("Send request must be approved")
        draft = self._get_owned_draft(request.draft_id, db=db, user=user)
        if self._draft_hash(draft) != request.content_hash:
            request.status = "rejected"; request.rejection_note = "草稿内容已变更，需要重新申请发送"; db.commit()
            raise ValueError("草稿内容已变更，需要重新申请发送")
        self._validate_policy(db=db, user=user, recipient=request.recipient, cc=request.cc)
        connector = self._get_smtp_connector(request.smtp_connector_id, db=db, user=user)
        policy = self._policy(db=db, organization_id=user.organization_id)
        dlp_result = self._scan_dlp(draft=draft, policy=policy)
        self._store_dlp_result(request, dlp_result)
        if dlp_result["blocked"]:
            request.status = "blocked"
            request.rejection_note = "外发内容命中高风险 DLP 策略，已阻断"
            db.commit()
            oplog_service.log(module="outbound_email", action="email_send_dlp_blocked", db=db, user_id=user.id, target_type="email_send_request", target_id=request.id, detail=f"draft_id={draft.id}; phase=execute; dlp={data_protection_service.audit_summary(dlp_result)}")
            raise ValueError("外发内容命中高风险 DLP 策略，已阻断")
        since = datetime.now(timezone.utc) - timedelta(hours=1)
        sent_count = db.query(EmailSendRequest).filter(EmailSendRequest.user_id == user.id, EmailSendRequest.status == "sent", EmailSendRequest.sent_at >= since).count()
        if sent_count >= policy.max_sends_per_hour:
            raise ValueError("已达到每小时发送上限")
        config = self._json(connector.config_json)
        credentials = mailbox_service.decrypt_credentials(connector.credential_ciphertext)
        message_id = make_msgid(domain=str(config.get("from_address") or "").split("@")[-1] or None)
        message = EmailMessage()
        message["From"] = str(config.get("from_address") or credentials.get("username"))
        message["To"] = request.recipient
        if request.cc:
            message["Cc"] = request.cc
        message["Subject"] = request.subject
        message["Message-ID"] = message_id
        message.set_content(draft.content)
        request.status = "sending"; db.commit()
        try:
            with smtplib.SMTP(str(config.get("host")), int(config.get("port") or 587), timeout=20) as client:
                client.ehlo()
                if config.get("use_starttls", True):
                    client.starttls(); client.ehlo()
                client.login(str(credentials.get("username") or ""), str(credentials.get("password") or ""))
                client.send_message(message)
            request.status = "sent"; request.sent_at = datetime.now(timezone.utc); request.provider_message_id = message_id; request.error_message = None
            # Billing drafts remain drafts until this successful provider handoff.
            # The metadata binding is written by BillingService and is deliberately
            # evaluated only after policy, DLP, approval and SMTP have all passed.
            metadata = self._json(draft.metadata_json)
            invoice_id = metadata.get("billing_invoice_id")
            if invoice_id:
                from app.models.legal_billing import LegalInvoice
                invoice = db.query(LegalInvoice).filter(LegalInvoice.id == invoice_id).first()
                if invoice and invoice.organization_id == request.organization_id and invoice.status == "draft":
                    invoice.status = "sent"
                    invoice.sent_at = request.sent_at
            db.commit(); db.refresh(request)
            oplog_service.log(module="outbound_email", action="email_sent", db=db, user_id=user.id, target_type="email_send_request", target_id=request.id, detail=f"draft_id={draft.id}; provider_message_id={message_id}")
            return request
        except Exception:
            request.status = "failed"; request.error_message = "SMTP 发送失败，请检查连接器与系统日志"; db.commit()
            oplog_service.log(module="outbound_email", action="email_send_failed", db=db, user_id=user.id, target_type="email_send_request", target_id=request.id, detail=f"draft_id={draft.id}; error=redacted")
            raise

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
        credentials = mailbox_service.decrypt_credentials(connector.credential_ciphertext)
        message = EmailMessage()
        message["From"] = str(config.get("from_address") or credentials.get("username"))
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(content)
        with smtplib.SMTP(str(config.get("host")), int(config.get("port") or 587), timeout=20) as client:
            client.ehlo()
            if config.get("use_starttls", True):
                client.starttls(); client.ehlo()
            client.login(str(credentials.get("username") or ""), str(credentials.get("password") or ""))
            client.send_message(message)
        db.add(draft)
        db.commit()
        oplog_service.log(module="portal", action="portal_otp_sent", db=db, user_id=user.id,
                          target_type="email_draft", target_id=draft.id, detail="transactional_otp=true")


outbound_email_service = OutboundEmailService()
