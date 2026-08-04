from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func

from app.core.database import Base


class ExternalConnector(Base):
    __tablename__ = "external_connectors"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    department_id = Column(Integer, ForeignKey("departments.id"), nullable=True, index=True)
    connector_type = Column(String(64), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    status = Column(String(32), nullable=False, default="active")
    config_json = Column(Text, nullable=True)
    credential_ciphertext = Column(Text, nullable=True)
    sync_cursor_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ConnectorSyncJob(Base):
    __tablename__ = "connector_sync_jobs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    connector_id = Column(Integer, ForeignKey("external_connectors.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="pending", index=True)
    sync_mode = Column(String(32), nullable=False, default="manual")
    result_summary = Column(Text, nullable=True)
    result_detail_json = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ConnectorOAuthState(Base):
    """One-time PKCE state for an administrator-initiated connector authorization."""

    __tablename__ = "connector_oauth_states"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    connector_id = Column(Integer, ForeignKey("external_connectors.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    state_hash = Column(String(128), nullable=False, unique=True, index=True)
    code_verifier_ciphertext = Column(Text, nullable=False)
    redirect_uri = Column(String(1024), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    consumed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class MailboxMessage(Base):
    __tablename__ = "mailbox_messages"
    __table_args__ = (UniqueConstraint("connector_id", "message_uid", name="uq_mailbox_messages_connector_uid"),)

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    connector_id = Column(Integer, ForeignKey("external_connectors.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    message_uid = Column(String(128), nullable=False)
    mailbox = Column(String(128), nullable=False, default="INBOX")
    thread_id = Column(String(256), nullable=True, index=True)
    sender = Column(String(512), nullable=True)
    recipient = Column(String(512), nullable=True)
    subject = Column(String(512), nullable=True)
    body_text = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    category = Column(String(32), nullable=False, default="other", index=True)
    importance = Column(String(32), nullable=False, default="normal", index=True)
    received_at = Column(DateTime(timezone=True), nullable=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
