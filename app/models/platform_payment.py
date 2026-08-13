"""#83/平台收款（对公转账）模型：企业客户 → 平台订阅付款"""
from sqlalchemy import Column, Integer, String, Numeric, Text, DateTime, ForeignKey, func
from app.core.database import Base


class PlatformPayment(Base):
    """#83/平台收款（对公转账）模型：企业客户 → 平台订阅付款。

    idempotency_key 防止重复提交；provider_event_id 关联支付回调（若走线上渠道）；
    refunded_amount 记录累计退款金额（并发安全，DB 层校验）。
    """
    __tablename__ = "platform_payments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    plan_tier = Column(String(16), nullable=False)
    amount = Column(Numeric(14, 2), nullable=False)
    currency = Column(String(8), nullable=False, server_default="CNY")
    status = Column(String(16), nullable=False, server_default="pending", index=True)
    voucher_no = Column(String(128), nullable=True)
    voucher_document_id = Column(Integer, ForeignKey("documents.id"), nullable=True)
    invoice_snapshot_json = Column(Text, nullable=True)
    note = Column(Text, nullable=True)
    confirmed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    # 支付可靠性与退款
    idempotency_key = Column(String(128), nullable=True, unique=True, index=True)
    provider = Column(String(64), nullable=True)
    provider_event_id = Column(String(128), nullable=True, index=True)
    refunded_amount = Column(Numeric(14, 2), nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
