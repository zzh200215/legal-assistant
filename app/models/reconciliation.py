"""每日对账：运行台账 + 结构化差异。

- run：cursor/checkpoint/租约，断点恢复；已成功 run 幂等跳过。
- discrepancy：expected/actual 金额、币种、状态、严重度、建议动作、处理状态。
- 不自动静默修改财务记录；仅已定义安全规则自动修复并记 adjustment。
"""

from sqlalchemy import Column, DateTime, Integer, Numeric, String, Text, func

from app.core.database import Base


class ReconciliationRun(Base):
    __tablename__ = "reconciliation_runs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    run_date = Column(String(10), nullable=False, index=True, comment="YYYY-MM-DD")
    provider = Column(String(32), nullable=False, index=True, comment="stripe / local")
    organization_id = Column(Integer, index=True, nullable=True, comment="NULL=全局")
    status = Column(String(16), nullable=False, default="pending", index=True,
                    comment="pending / running / succeeded / failed")
    cursor_json = Column(Text, nullable=True)
    checkpoint_json = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    processed = Column(Integer, nullable=False, default=0)
    discrepancies_found = Column(Integer, nullable=False, default=0)
    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    idempotency_key = Column(String(128), nullable=True, index=True)
    lease_owner = Column(String(128), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ReconciliationDiscrepancy(Base):
    __tablename__ = "reconciliation_discrepancies"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    run_id = Column(Integer, index=True, nullable=True)
    discrepancy_type = Column(String(64), nullable=False, index=True,
                              comment="webhook_pending / payment_stuck / invoice_amount_mismatch / refund_mismatch / amount_mismatch / status_mismatch")
    local_reference = Column(String(256), nullable=True, comment="本地对象引用（表+id）")
    provider_reference = Column(String(256), nullable=True, comment="供应商对象引用")
    expected_amount = Column(Numeric(18, 6), nullable=True)
    actual_amount = Column(Numeric(18, 6), nullable=True)
    currency = Column(String(8), nullable=True)
    expected_status = Column(String(32), nullable=True)
    actual_status = Column(String(32), nullable=True)
    severity = Column(String(16), nullable=False, default="medium",
                      comment="low / medium / high / critical")
    status = Column(String(16), nullable=False, default="open",
                    comment="open / auto_fixed / manual_reviewed / closed")
    recommended_action = Column(String(512), nullable=True)
    detected_at = Column(DateTime(timezone=True), nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolution_note = Column(Text, nullable=True)
