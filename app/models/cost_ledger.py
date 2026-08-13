"""不可变成本台账（Cost Ledger）。

追加式记录，禁止覆盖/删除既有财务记录；修正使用 adjustment / reversal。
- 金额统一 Decimal + Numeric(18,6)，禁止 float。
- UNIQUE(scope, idempotency_key)：同一来源事件重复处理只生成一条。
- direction: cost / charge / payment / refund / adjustment。
"""

from sqlalchemy import Column, DateTime, Integer, Numeric, String, Text, UniqueConstraint, func

from app.core.database import Base


class CostLedgerEntry(Base):
    __tablename__ = "cost_ledger"
    __table_args__ = (
        UniqueConstraint("scope", "idempotency_key", name="uq_cost_ledger_scope_key"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    entry_id = Column(String(64), nullable=False, unique=True, index=True, comment="台账条目 ID")
    tenant_id = Column(Integer, index=True, nullable=True, comment="organization_id")
    user_id = Column(Integer, index=True, nullable=True)
    entry_type = Column(String(64), nullable=False, index=True,
                        comment="llm_call / storage / invoice / payment / refund / plan_subscription / adjustment")
    direction = Column(String(16), nullable=False,
                       comment="cost / charge / payment / refund / adjustment")
    amount = Column(Numeric(18, 6), nullable=False)
    currency = Column(String(8), nullable=False, default="CNY")
    quantity = Column(Numeric(18, 6), nullable=True)
    unit = Column(String(64), nullable=True)
    unit_price = Column(Numeric(18, 6), nullable=True)
    source_type = Column(String(64), nullable=True, comment="llm_run / storage_object / invoice / payment / refund")
    source_id = Column(String(128), nullable=True, index=True)
    billing_period = Column(String(7), nullable=True, comment="YYYY-MM")
    scope = Column(String(32), nullable=False)
    idempotency_key = Column(String(128), nullable=False)
    metadata_summary = Column(Text, nullable=True, comment="脱敏摘要，不含敏感内容")
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
