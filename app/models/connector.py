"""External connector model — used as the legal notification channel backend.

Only ``ExternalConnector`` is retained: the legal notification service uses it to
decide whether Feishu / WeCom channels are available, and the outbound email
service uses it as the SMTP delivery connector. The office document-source,
mailbox and OAuth models were removed.
"""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func

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
