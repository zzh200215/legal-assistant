"""订阅计划与配额模型"""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, func, Boolean, Text, Numeric
from app.core.database import Base
import enum


class PlanTier(str, enum.Enum):
    free = "free"
    pro = "pro"
    team = "team"


class SubscriptionStatus(str, enum.Enum):
    active = "active"
    expired = "expired"
    cancelled = "cancelled"
    pending = "pending"


PLAN_QUOTAS = {
    PlanTier.free: {
        "consultation": 5,
        "review": 2,
        "draft": 2,
    },
    PlanTier.pro: {
        "consultation": 50,
        "review": 20,
        "draft": 20,
    },
    # 团队版"无限"已按单位经济核算改为合同化固定上限（M-1，2026-08-02）。
    # 依据：真实库单次咨询≈0.012元、单次合同审查全流程≈0.10-0.15元；
    # 上限按最坏成本 5000×0.03 + 2000×0.15 ≈ 450元/月，仍远低于 999元/月售价。
    PlanTier.team: {
        "consultation": 5000,
        "review": 2000,
        "draft": 2000,
    },
}


class SubscriptionPlan(Base):
    """订阅计划定义"""
    __tablename__ = "subscription_plans"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tier = Column(String(32), unique=True, nullable=False, index=True)
    name = Column(String(64), nullable=False)
    description = Column(Text, nullable=True)
    price_monthly = Column(Numeric(10, 2), nullable=False, default=0)
    quota_consultation = Column(Integer, nullable=False, default=5)
    quota_review = Column(Integer, nullable=False, default=2)
    quota_draft = Column(Integer, nullable=False, default=2)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class UserSubscription(Base):
    """用户订阅记录"""
    __tablename__ = "user_subscriptions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    plan_id = Column(Integer, ForeignKey("subscription_plans.id"), nullable=False)
    status = Column(String(32), default=SubscriptionStatus.active.value, nullable=False, index=True)
    # 支付信息
    payment_provider = Column(String(32), nullable=True)   # stripe / pingpp
    payment_subscription_id = Column(String(128), nullable=True, index=True)
    payment_customer_id = Column(String(128), nullable=True)
    # 周期
    current_period_start = Column(DateTime(timezone=True), nullable=True)
    current_period_end = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class QuotaUsage(Base):
    """月度配额用量"""
    __tablename__ = "quota_usages"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    year_month = Column(String(7), nullable=False, index=True)  # "2026-07"
    consultation_count = Column(Integer, default=0, nullable=False)
    review_count = Column(Integer, default=0, nullable=False)
    draft_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
