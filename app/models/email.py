from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, func
from app.core.database import Base


class EmailDraft(Base):
    __tablename__ = "email_drafts"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    department_id = Column(Integer, ForeignKey("departments.id"), nullable=True, index=True)
    subject = Column(String(512), nullable=False)
    recipient = Column(String(256), nullable=True)
    cc = Column(String(512), nullable=True)
    content = Column(Text, nullable=False)
    purpose = Column(String(512), nullable=True)
    key_points = Column(Text, nullable=True)
    need_action = Column(Boolean, nullable=True)
    generation_type = Column(String(32), default="generate", nullable=False)
    original_email = Column(Text, nullable=True)
    reply_goal = Column(String(512), nullable=True)
    tone = Column(String(32), default="professional", nullable=False)
    status = Column(String(32), default="draft", nullable=False)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class OutboundEmailPolicy(Base):
    __tablename__ = "outbound_email_policies"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, unique=True, index=True)
    enabled = Column(Boolean, nullable=False, default=False)
    allowed_recipient_domains_json = Column(Text, nullable=True)
    max_sends_per_hour = Column(Integer, nullable=False, default=20)
    require_approval = Column(Boolean, nullable=False, default=True)
    dlp_enabled = Column(Boolean, nullable=False, default=True)
    dlp_action = Column(String(16), nullable=False, default="block")
    updated_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class EmailSendRequest(Base):
    """邮件发送请求：同时承担「邮件 Outbox」角色。

    业务事务内与 EmailDraft 一同创建；投递由 worker 领取（claim）后执行。
    状态机（draft→requested→approved→sending→sent/failed，failed→requested/
    dead_letter）由 outbound_email_service 集中校验。
    """
    __tablename__ = "email_send_requests"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    draft_id = Column(Integer, ForeignKey("email_drafts.id"), nullable=False, index=True)
    smtp_connector_id = Column(Integer, ForeignKey("external_connectors.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    recipient = Column(String(512), nullable=False)
    cc = Column(String(512), nullable=True)
    bcc = Column(String(512), nullable=True)
    subject = Column(String(512), nullable=False)
    content_hash = Column(String(64), nullable=False)
    idempotency_key = Column(String(128), nullable=False, unique=True, index=True)
    status = Column(String(32), nullable=False, default="pending", index=True,
                    comment="pending/requested / approved / rejected / sending / sent / failed / blocked / dead_letter")
    approved_at = Column(DateTime(timezone=True), nullable=True)
    approved_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    rejection_note = Column(Text, nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    provider_message_id = Column(String(256), nullable=True, index=True)
    error_message = Column(Text, nullable=True)
    dlp_status = Column(String(32), nullable=False, default="not_scanned", index=True)
    dlp_findings_json = Column(Text, nullable=True)
    dlp_scanned_at = Column(DateTime(timezone=True), nullable=True)
    # 投递可靠性（Outbox 领取 / 重试 / 死信）
    attempt = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=True)
    next_retry_at = Column(DateTime(timezone=True), nullable=True, index=True)
    claimed_by = Column(String(128), nullable=True, comment="持有投递的 worker/run 标识")
    claim_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    error_code = Column(String(64), nullable=True, comment="稳定业务错误码，脱敏")
    sanitized_error_message = Column(Text, nullable=True)
    dead_letter_at = Column(DateTime(timezone=True), nullable=True)
    dead_letter_reason = Column(String(512), nullable=True)
    # 通知事件回链：邮件投递终态镜像回通知事件
    notification_event_id = Column(Integer, ForeignKey("legal_notification_events.id"),
                                   nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class EmailAttachment(Base):
    """邮件附件台账：DLP 与安全扫描结果、对象存储 key、处理状态。

    附件流式保存到对象存储；扫描失败或高风险附件隔离（不进入发送链路）。
    不记录附件内容本身，仅保存摘要/哈希/元数据。
    """
    __tablename__ = "email_attachments"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    draft_id = Column(Integer, ForeignKey("email_drafts.id"), nullable=True, index=True)
    send_request_id = Column(Integer, ForeignKey("email_send_requests.id"), nullable=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    filename = Column(String(512), nullable=False)
    mime_type = Column(String(128), nullable=True)
    size_bytes = Column(Integer, nullable=False, default=0)
    content_hash = Column(String(64), nullable=False, index=True)
    storage_key = Column(String(512), nullable=True, comment="对象存储 key")
    scan_status = Column(String(32), nullable=False, default="not_scanned", index=True,
                         comment="not_scanned / clean / blocked / quarantined / error")
    scan_result_json = Column(Text, nullable=True, comment="脱敏命中摘要，不含敏感命中内容")
    scan_scanner_version = Column(String(64), nullable=True)
    scanned_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
