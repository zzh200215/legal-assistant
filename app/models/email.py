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
    __tablename__ = "email_send_requests"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    draft_id = Column(Integer, ForeignKey("email_drafts.id"), nullable=False, index=True)
    smtp_connector_id = Column(Integer, ForeignKey("external_connectors.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    recipient = Column(String(512), nullable=False)
    cc = Column(String(512), nullable=True)
    subject = Column(String(512), nullable=False)
    content_hash = Column(String(64), nullable=False)
    idempotency_key = Column(String(128), nullable=False, unique=True, index=True)
    status = Column(String(32), nullable=False, default="pending", index=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    approved_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    rejection_note = Column(Text, nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    provider_message_id = Column(String(256), nullable=True, index=True)
    error_message = Column(Text, nullable=True)
    dlp_status = Column(String(32), nullable=False, default="not_scanned", index=True)
    dlp_findings_json = Column(Text, nullable=True)
    dlp_scanned_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
