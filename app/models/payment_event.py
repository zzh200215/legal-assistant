"""支付 Webhook 事件持久化（幂等 + 乱序保护 + 可重放）。

- UNIQUE(provider, provider_event_id)：同一供应商事件只处理一次。
- 仅保存脱敏 payload 哈希/事件类型/对象/时间，不保存完整原始载荷与密钥。
- status: pending -> processing -> completed / failed / needs_reconciliation。
"""

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint, func

from app.core.database import Base


class PaymentEvent(Base):
    __tablename__ = "payment_events"
    __table_args__ = (
        UniqueConstraint("provider", "provider_event_id",
                         name="uq_payment_events_provider_event_id"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    provider = Column(String(32), nullable=False, index=True, comment="stripe / pingpp / ...")
    provider_event_id = Column(String(128), nullable=False, comment="供应商事件唯一 ID")
    event_type = Column(String(64), nullable=False, index=True)
    raw_payload_hash = Column(String(64), nullable=False, comment="原始载荷 SHA-256，不存明文")
    sanitized_payload_json = Column(Text, nullable=True, comment="脱敏后处理所需载荷（无卡号/密钥）")
    object_type = Column(String(64), nullable=True, comment="subscription / charge / ...")
    object_id = Column(String(128), nullable=True, index=True, comment="供应商对象 ID")
    occurred_at = Column(DateTime(timezone=True), nullable=True, comment="供应商事件发生时间")
    received_at = Column(DateTime(timezone=True), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="pending", index=True,
                    comment="pending / processing / completed / failed / needs_reconciliation")
    attempt = Column(Integer, nullable=False, default=0)
    next_retry_at = Column(DateTime(timezone=True), nullable=True, index=True)
    claimed_by = Column(String(128), nullable=True)
    claim_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    error_code = Column(String(64), nullable=True)
    error_summary = Column(Text, nullable=True, comment="脱敏错误摘要")
    processed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
