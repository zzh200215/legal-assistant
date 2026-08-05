"""订阅与配额服务"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import and_
from app.core.time import utc_now
from app.core.config import get_settings

from app.models.subscription import (
    SubscriptionPlan, UserSubscription, QuotaUsage,
    PlanTier, SubscriptionStatus, PLAN_QUOTAS,
)
from app.models.user import User


QuotaType = str   # "consultation" | "review" | "draft"


class SubscriptionService:

    def _current_month(self) -> str:
        return utc_now().strftime("%Y-%m")

    def get_active_subscription(self, db: Session, user_id: int) -> Optional[UserSubscription]:
        """获取用户当前有效订阅"""
        return db.query(UserSubscription).filter(
            UserSubscription.user_id == user_id,
            UserSubscription.status == SubscriptionStatus.active.value,
        ).order_by(UserSubscription.id.desc()).first()

    def get_user_plan(self, db: Session, user_id: int) -> SubscriptionPlan:
        """获取用户当前计划，无订阅则返回免费计划"""
        sub = self.get_active_subscription(db, user_id)
        if sub:
            plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == sub.plan_id).first()
            if plan:
                return plan

        free_plan = db.query(SubscriptionPlan).filter(
            SubscriptionPlan.tier == PlanTier.free.value
        ).first()
        return free_plan

    def get_or_create_usage(self, db: Session, user_id: int) -> QuotaUsage:
        """获取或创建当月用量记录"""
        month = self._current_month()
        usage = db.query(QuotaUsage).filter(
            QuotaUsage.user_id == user_id,
            QuotaUsage.year_month == month,
        ).first()

        if not usage:
            usage = QuotaUsage(user_id=user_id, year_month=month)
            db.add(usage)
            db.commit()
            db.refresh(usage)

        return usage

    def check_quota(self, db: Session, user_id: int, quota_type: QuotaType) -> bool:
        """检查用户是否还有配额。返回 True 表示可以继续，False 表示超配额。"""
        plan = self.get_user_plan(db, user_id)
        if plan is None:
            # 无计划定义时拒绝
            return False

        # 获取计划配额上限
        quota_field = {
            "consultation": plan.quota_consultation,
            "review": plan.quota_review,
            "draft": plan.quota_draft,
        }.get(quota_type)

        if quota_field is None:
            return False

        if quota_field == -1:
            # unlimited
            return True

        # 获取当月已用量
        usage = self.get_or_create_usage(db, user_id)
        used = {
            "consultation": usage.consultation_count,
            "review": usage.review_count,
            "draft": usage.draft_count,
        }.get(quota_type, 0)

        return used < quota_field

    def record_usage(self, db: Session, user_id: int, quota_type: QuotaType) -> QuotaUsage:
        """记录一次使用"""
        usage = self.get_or_create_usage(db, user_id)

        if quota_type == "consultation":
            usage.consultation_count += 1
        elif quota_type == "review":
            usage.review_count += 1
        elif quota_type == "draft":
            usage.draft_count += 1

        db.add(usage)
        db.commit()
        db.refresh(usage)
        return usage

    def get_usage_summary(self, db: Session, user_id: int) -> dict:
        """获取当月配额用量摘要"""
        plan = self.get_user_plan(db, user_id)
        usage = self.get_or_create_usage(db, user_id)

        def _fmt(used: int, quota: int) -> dict:
            return {
                "used": used,
                "quota": quota,
                "unlimited": quota == -1,
                "remaining": max(0, quota - used) if quota != -1 else None,
            }

        return {
            "year_month": usage.year_month,
            "plan_tier": plan.tier if plan else "free",
            "consultation": _fmt(usage.consultation_count, plan.quota_consultation if plan else 5),
            "review": _fmt(usage.review_count, plan.quota_review if plan else 2),
            "draft": _fmt(usage.draft_count, plan.quota_draft if plan else 2),
        }

    def activate_subscription(
        self,
        db: Session,
        user_id: int,
        plan_tier: str,
        payment_provider: str,
        payment_subscription_id: str,
        payment_customer_id: Optional[str] = None,
        period_start: Optional[datetime] = None,
        period_end: Optional[datetime] = None,
    ) -> UserSubscription:
        """支付成功后激活订阅（取消旧订阅）"""
        # 取消现有活跃订阅
        db.query(UserSubscription).filter(
            UserSubscription.user_id == user_id,
            UserSubscription.status == SubscriptionStatus.active.value,
        ).update({"status": SubscriptionStatus.cancelled.value})

        # 获取计划
        plan = db.query(SubscriptionPlan).filter(
            SubscriptionPlan.tier == plan_tier
        ).first()
        if not plan:
            raise ValueError(f"Unknown plan tier: {plan_tier}")

        sub = UserSubscription(
            user_id=user_id,
            plan_id=plan.id,
            status=SubscriptionStatus.active.value,
            payment_provider=payment_provider,
            payment_subscription_id=payment_subscription_id,
            payment_customer_id=payment_customer_id,
            current_period_start=period_start,
            current_period_end=period_end,
        )
        db.add(sub)
        db.commit()
        db.refresh(sub)
        return sub

    def cancel_subscription(self, db: Session, user_id: int) -> Optional[UserSubscription]:
        """取消用户订阅（降回免费）"""
        sub = self.get_active_subscription(db, user_id)
        if not sub:
            return None

        sub.status = SubscriptionStatus.cancelled.value
        sub.cancelled_at = datetime.now(timezone.utc)
        db.add(sub)
        db.commit()
        db.refresh(sub)
        return sub

    def expire_overdue_subscriptions(self, db: Session) -> int:
        """周期任务：将已过 current_period_end 的 active 订阅置为 expired。

        过期后 get_active_subscription 返回 None，用户配额自动回落到免费版
        （check_quota 依据 get_user_plan 的 free 计划）。
        """
        now = datetime.now(timezone.utc)
        overdue = db.query(UserSubscription).filter(
            UserSubscription.status == SubscriptionStatus.active.value,
            UserSubscription.current_period_end.isnot(None),
            UserSubscription.current_period_end < now,
        ).all()
        for sub in overdue:
            sub.status = SubscriptionStatus.expired.value
            sub.cancelled_at = now
        if overdue:
            db.commit()
        return len(overdue)

    def ensure_default_plans(self, db: Session) -> None:
        """确保默认计划存在（应用启动时调用）。

        M-3：free 档位配额从 settings 读取（FREE_PLAN_*_QUOTA），且对已存在的
        free 行同步配额，使 A/B 档位（5→8）参数化后无需迁移即可生效。
        """
        _settings = get_settings()
        defaults = [
            {
                "tier": PlanTier.free.value,
                "name": "免费版",
                "description": f"每月咨询{_settings.FREE_PLAN_CONSULTATION_QUOTA}次、"
                               f"合同审查{_settings.FREE_PLAN_REVIEW_QUOTA}次、"
                               f"文书生成{_settings.FREE_PLAN_DRAFT_QUOTA}次",
                "price_monthly": 0,
                "quota_consultation": _settings.FREE_PLAN_CONSULTATION_QUOTA,
                "quota_review": _settings.FREE_PLAN_REVIEW_QUOTA,
                "quota_draft": _settings.FREE_PLAN_DRAFT_QUOTA,
            },
            {
                "tier": PlanTier.pro.value,
                "name": "专业版",
                "description": "每月咨询50次、合同审查20次、文书生成20次",
                "price_monthly": 199,
                "quota_consultation": PLAN_QUOTAS[PlanTier.pro]["consultation"],
                "quota_review": PLAN_QUOTAS[PlanTier.pro]["review"],
                "quota_draft": PLAN_QUOTAS[PlanTier.pro]["draft"],
            },
            {
                "tier": PlanTier.team.value,
                "name": "团队版",
                "description": "每月咨询5000次、合同审查2000次、文书生成2000次，团队协作功能全开",
                "price_monthly": 999,
                "quota_consultation": PLAN_QUOTAS[PlanTier.team]["consultation"],
                "quota_review": PLAN_QUOTAS[PlanTier.team]["review"],
                "quota_draft": PLAN_QUOTAS[PlanTier.team]["draft"],
            },
        ]
        for d in defaults:
            plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.tier == d["tier"]).first()
            if not plan:
                db.add(SubscriptionPlan(**d))
            elif d["tier"] == PlanTier.free.value:
                # free 档位随配置同步（M-3 参数化）
                plan.description = d["description"]
                plan.quota_consultation = d["quota_consultation"]
                plan.quota_review = d["quota_review"]
                plan.quota_draft = d["quota_draft"]
        db.commit()


subscription_service = SubscriptionService()
