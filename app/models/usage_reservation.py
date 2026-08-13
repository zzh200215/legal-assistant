"""配额预留（reserve / commit / release）。

- UNIQUE(usage_event_id)：同一消耗事件只允许一条预留，重复处理不重复扣减。
- 扣减经 quota_usages 条件 UPDATE 原子完成；失败置 released 并回滚消耗。
- status: reserved -> committed / released / expired。
"""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint, func

from app.core.database import Base


class UsageReservation(Base):
    __tablename__ = "usage_reservations"
    __table_args__ = (
        UniqueConstraint("usage_event_id", name="uq_usage_reservations_event_id"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    tenant_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    quota_type = Column(String(32), nullable=False, comment="consultation / review / draft")
    quantity = Column(Integer, nullable=False, default=1)
    status = Column(String(16), nullable=False, default="reserved", index=True,
                    comment="reserved / committed / released / expired")
    usage_event_id = Column(String(128), nullable=False, comment="稳定消耗事件 ID")
    billing_period = Column(String(7), nullable=False, comment="YYYY-MM")
    source_type = Column(String(64), nullable=True)
    source_id = Column(String(128), nullable=True, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
