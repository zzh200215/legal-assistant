"""Phase 11 — 计时计费领域模型

legal_time_entries / legal_billing_rules / legal_invoices / legal_invoice_items /
legal_payment_records / legal_refund_records / legal_collection_reminders
"""

from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, func

from app.core.database import Base
from app.core.encryption import EncryptedText


class LegalTimeEntry(Base):
    __tablename__ = "legal_time_entries"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    case_id = Column(Integer, ForeignKey("legal_cases.id"), nullable=False, index=True)
    operator_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True, comment="计时操作人")
    billing_rule_id = Column(Integer, ForeignKey("legal_billing_rules.id"), nullable=True, index=True)
    # 手工补录时 started_at + ended_at 必填；实时计时时 started_at 在开始时记录
    started_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    duration_minutes = Column(Integer, nullable=True, comment="计算后分钟数，结束后由服务层写入")
    status = Column(String(16), nullable=False, default="running", index=True,
                    comment="running / paused / completed / voided")
    description = Column(String(500), nullable=False, default="", comment="工作说明，1-500字")
    hourly_rate = Column(Numeric(12, 2), nullable=True, comment="快照费率，确认可计费后固化")
    billed_amount = Column(Numeric(12, 2), nullable=True, comment="实际计费金额快照")
    billable = Column(Integer, nullable=False, default=0, comment="0=待确认 1=可计费 2=不计费")
    confirmed_by = Column(Integer, ForeignKey("users.id"), nullable=True, comment="reviewer/admin 确认人")
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    idempotency_key = Column(String(64), nullable=True, unique=True, index=True, comment="防重创建键")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LegalBillingRule(Base):
    __tablename__ = "legal_billing_rules"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    case_id = Column(Integer, ForeignKey("legal_cases.id"), nullable=True, index=True,
                     comment="NULL=组织默认规则，非NULL=案件专属规则")
    name = Column(String(128), nullable=False)
    billing_mode = Column(String(16), nullable=False, comment="hourly / fixed_stage / hybrid")
    hourly_rate = Column(Numeric(12, 2), nullable=True, comment="小时费率，hourly/hybrid模式下必填")
    fixed_amount = Column(Numeric(12, 2), nullable=True, comment="阶段固定费，fixed_stage/hybrid模式下必填")
    currency = Column(String(8), nullable=False, default="CNY")
    effective_from = Column(Date, nullable=True)
    effective_to = Column(Date, nullable=True)
    is_active = Column(Integer, nullable=False, default=1)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LegalInvoice(Base):
    __tablename__ = "legal_invoices"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    case_id = Column(Integer, ForeignKey("legal_cases.id"), nullable=False, index=True)
    invoice_no = Column(String(64), nullable=False, unique=True, index=True, comment="账单编号，唯一且不可变")
    client_display_name = Column(String(256), nullable=False)
    client_contact = Column(EncryptedText, nullable=True, comment="客户联系方式（AES-256-GCM）")
    issue_date = Column(Date, nullable=False)
    due_date = Column(Date, nullable=True)
    billing_period_start = Column(Date, nullable=True)
    billing_period_end = Column(Date, nullable=True)
    subtotal = Column(Numeric(14, 2), nullable=False, default=0)
    tax_amount = Column(Numeric(14, 2), nullable=False, default=0)
    discount_amount = Column(Numeric(14, 2), nullable=False, default=0)
    total_amount = Column(Numeric(14, 2), nullable=False, default=0)
    currency = Column(String(8), nullable=False, default="CNY")
    # 不可变账单快照：价格/税费/周期/客户信息，出账后不受后续变更影响
    price_snapshot_json = Column(Text, nullable=True, comment="套餐/单价/数量/折扣快照")
    tax_snapshot_json = Column(Text, nullable=True, comment="税率/税额/税务地区快照")
    snapshot_hash = Column(String(64), nullable=True, comment="快照内容 SHA-256")
    # 收款进度：unpaid / partial_paid / fully_paid / refunding / refunded
    payment_progress = Column(String(16), nullable=False, default="unpaid", index=True)
    status = Column(String(16), nullable=False, default="draft", index=True,
                    comment="draft / sent / paid / overdue / voided")
    pdf_path = Column(String(512), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    voided_at = Column(DateTime(timezone=True), nullable=True)
    void_reason = Column(Text, nullable=True)
    original_invoice_id = Column(Integer, ForeignKey("legal_invoices.id"), nullable=True,
                                  comment="更正账单关联的原账单ID")
    collection_count = Column(Integer, nullable=False, default=0, comment="已发送催收次数")
    idempotency_key = Column(String(64), nullable=True, unique=True, index=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LegalInvoiceItem(Base):
    __tablename__ = "legal_invoice_items"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    invoice_id = Column(Integer, ForeignKey("legal_invoices.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    time_entry_id = Column(Integer, ForeignKey("legal_time_entries.id"), nullable=True, index=True,
                           comment="关联的计时条目，NULL=手动明细")
    title = Column(String(256), nullable=False)
    description = Column(Text, nullable=True)
    duration_minutes = Column(Integer, nullable=True)
    unit_price = Column(Numeric(12, 2), nullable=False)
    quantity = Column(Numeric(10, 2), nullable=False, default=1)
    discount_rate = Column(Numeric(5, 4), nullable=False, default=0, comment="折扣率 0.00-1.00")
    amount = Column(Numeric(14, 2), nullable=False, comment="明细金额快照，出账后不变")
    snapshot_at = Column(DateTime(timezone=True), server_default=func.now(), comment="明细固化时间戳")


class LegalPaymentRecord(Base):
    __tablename__ = "legal_payment_records"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    invoice_id = Column(Integer, ForeignKey("legal_invoices.id"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    amount = Column(Numeric(14, 2), nullable=False)
    currency = Column(String(8), nullable=False, default="CNY")
    payment_method = Column(String(16), nullable=False, comment="provider / bank_transfer / cash / other")
    transaction_id = Column(String(128), nullable=True, index=True, comment="外部支付流水号，同一provider内唯一")
    provider = Column(String(64), nullable=True, comment="支付服务商标识")
    status = Column(String(16), nullable=False, default="confirmed", comment="confirmed / refunded / disputed")
    note = Column(Text, nullable=True)
    voucher_document_id = Column(Integer, ForeignKey("documents.id"), nullable=True, comment="付款凭证文档")
    recorded_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    recorded_at = Column(DateTime(timezone=True), server_default=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class LegalRefundRecord(Base):
    __tablename__ = "legal_refund_records"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    invoice_id = Column(Integer, ForeignKey("legal_invoices.id"), nullable=False, index=True)
    payment_record_id = Column(Integer, ForeignKey("legal_payment_records.id"), nullable=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    amount = Column(Numeric(14, 2), nullable=False)
    reason = Column(Text, nullable=False)
    status = Column(String(16), nullable=False, default="pending", comment="pending / approved / rejected / completed")
    provider_refund_id = Column(String(128), nullable=True, index=True, comment="供应商退款 ID")
    recorded_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class LegalCollectionReminder(Base):
    __tablename__ = "legal_collection_reminders"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    invoice_id = Column(Integer, ForeignKey("legal_invoices.id"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    status = Column(String(16), nullable=False, default="draft", comment="draft / sent / failed")
    scheduled_at = Column(DateTime(timezone=True), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    note = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
