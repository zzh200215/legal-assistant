"""#83/平台收款（对公转账）模型：企业客户 → 平台订阅付款"""
from sqlalchemy import Column, Integer, String, Numeric, Text, DateTime, ForeignKey, func
from app.core.database import Base


class PlatformPayment(Base):
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
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
