"""订阅计划与配额模型"""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, func, Boolean, Text, Numeric, UniqueConstraint
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
    past_due = "past_due"
    suspended = "suspended"
    trialing = "trialing"


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
    """订阅计划定义（价格/配额变更只新建版本，不原地覆盖影响历史账单）"""
    __tablename__ = "subscription_plans"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tier = Column(String(32), unique=True, nullable=False, index=True)
    name = Column(String(64), nullable=False)
    description = Column(Text, nullable=True)
    price_monthly = Column(Numeric(10, 2), nullable=False, default=0)
    quota_consultation = Column(Integer, nullable=False, default=5)
    quota_review = Column(Integer, nullable=False, default=2)
    quota_draft = Column(Integer, nullable=False, default=2)
    # 价格/配额版本：变更时递增，历史订阅经 snapshot 引用
    price_version = Column(Integer, nullable=False, default=1)
    currency = Column(String(8), nullable=False, default="CNY")
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SubscriptionPlanVersion(Base):
    """套餐版本快照：价格/配额/币种，供历史订阅与账单追溯（不可变）。"""
    __tablename__ = "subscription_plan_versions"
    __table_args__ = (
        UniqueConstraint("plan_id", "price_version", name="uq_plan_versions_plan_id_version"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    plan_id = Column(Integer, ForeignKey("subscription_plans.id"), nullable=False, index=True)
    tier = Column(String(32), nullable=False, index=True)
    price_version = Column(Integer, nullable=False)
    price_monthly = Column(Numeric(10, 2), nullable=False, default=0)
    quota_consultation = Column(Integer, nullable=False, default=5)
    quota_review = Column(Integer, nullable=False, default=2)
    quota_draft = Column(Integer, nullable=False, default=2)
    currency = Column(String(8), nullable=False, default="CNY")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class UserSubscription(Base):
    """用户订阅记录（plan_version 引用套餐版本快照，价格/配额变更不影响已授权益）"""
    __tablename__ = "user_subscriptions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    plan_id = Column(Integer, ForeignKey("subscription_plans.id"), nullable=False)
    plan_version = Column(Integer, nullable=False, default=1, comment="授时套餐版本快照")
    status = Column(String(32), default=SubscriptionStatus.active.value, nullable=False, index=True)
    # 支付信息
    payment_provider = Column(String(32), nullable=True)   # stripe / pingpp / bank_transfer
    payment_subscription_id = Column(String(128), nullable=True, index=True)
    payment_customer_id = Column(String(128), nullable=True)
    # 周期
    current_period_start = Column(DateTime(timezone=True), nullable=True)
    current_period_end = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    # 幂等键：同一支付/回调只激活一次
    idempotency_key = Column(String(128), nullable=True, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class QuotaUsage(Base):
    """月度配额用量（UNIQUE(user, month) 支持原子扣减）"""
    __tablename__ = "quota_usages"
    __table_args__ = (
        UniqueConstraint("user_id", "year_month", name="uq_quota_usages_user_month"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    year_month = Column(String(7), nullable=False, index=True)  # "2026-07"
    consultation_count = Column(Integer, default=0, nullable=False)
    review_count = Column(Integer, default=0, nullable=False)
    draft_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
