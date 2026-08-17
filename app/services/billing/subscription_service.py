"""订阅与配额服务（订阅状态机 + 套餐版本快照 + 原子配额）"""
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.time import utc_now
from app.models.subscription import (
    SubscriptionPlan, SubscriptionPlanVersion, UserSubscription, QuotaUsage,
    PlanTier, SubscriptionStatus, PLAN_QUOTAS,
)
from app.models.usage_reservation import UsageReservation
from app.models.user import User
from app.services.billing.billing_state_machines import (
    reservation_transition, subscription_transition,
    SUB_CANCELLED, SUB_EXPIRED,
)
from app.services.observability.oplog_service import oplog_service


QuotaType = str   # "consultation" | "review" | "draft"


class QuotaExceededError(ValueError):
    """配额不足，携带稳定错误码。"""

    def __init__(self, code: str = "QUOTA_EXCEEDED", message: str = "配额已用完") -> None:
        self.code = code
        super().__init__(message)


class SubscriptionService:

    def _current_month(self) -> str:
        return utc_now().strftime("%Y-%m")

    # ── 套餐版本快照 ──────────────────────────────────────────────

    def snapshot_plan(self, db: Session, plan: SubscriptionPlan) -> SubscriptionPlanVersion:
        """确保 plan 当前 price_version 的快照存在（get-or-create）。"""
        version = (
            db.query(SubscriptionPlanVersion)
            .filter(
                SubscriptionPlanVersion.plan_id == plan.id,
                SubscriptionPlanVersion.price_version == plan.price_version,
            )
            .first()
        )
        if version is not None:
            return version
        version = SubscriptionPlanVersion(
            plan_id=plan.id,
            tier=plan.tier,
            price_version=plan.price_version,
            price_monthly=plan.price_monthly,
            quota_consultation=plan.quota_consultation,
            quota_review=plan.quota_review,
            quota_draft=plan.quota_draft,
            currency=plan.currency or "CNY",
        )
        db.add(version)
        db.flush()
        return version

    def get_plan_snapshot(self, db: Session, sub: UserSubscription) -> SubscriptionPlanVersion | None:
        """按订阅记录的 plan_version 解析授时套餐快照（历史权益不受后续套餐变更影响）。"""
        return (
            db.query(SubscriptionPlanVersion)
            .filter(
                SubscriptionPlanVersion.plan_id == sub.plan_id,
                SubscriptionPlanVersion.price_version == sub.plan_version,
            )
            .first()
        )

    def get_user_plan(self, db: Session, user_id: int) -> SubscriptionPlan:
        """获取用户当前计划（活跃订阅按其授时套餐版本快照），无订阅返回免费计划。"""
        sub = self.get_active_subscription(db, user_id)
        if sub:
            snapshot = self.get_plan_snapshot(db, sub)
            if snapshot is not None:
                return snapshot
            plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == sub.plan_id).first()
            if plan:
                return plan

        free_plan = db.query(SubscriptionPlan).filter(
            SubscriptionPlan.tier == PlanTier.free.value
        ).first()
        return free_plan

    # ── 订阅状态机 ──────────────────────────────────────────────

    def transition_subscription(self, *, db: Session, sub: UserSubscription, to: str,
                                reason: str | None = None, actor_id: int | None = None) -> None:
        """集中校验订阅状态迁移，记录审计。"""
        subscription_transition(sub.status, to)
        sub.status = to
        oplog_service.log(module="subscription", action=f"subscription_{to}", db=db,
                          user_id=actor_id or sub.user_id, target_type="user_subscription",
                          target_id=sub.id,
                          detail=f"from={sub.status}; reason={reason or ''}")

    def get_active_subscription(self, db: Session, user_id: int) -> Optional[UserSubscription]:
        """获取用户当前有效订阅"""
        return db.query(UserSubscription).filter(
            UserSubscription.user_id == user_id,
            UserSubscription.status.in_([SubscriptionStatus.active.value,
                                         SubscriptionStatus.past_due.value,
                                         SubscriptionStatus.suspended.value]),
        ).order_by(UserSubscription.id.desc()).first()

    # ── 配额（原子扣减）────────────────────────────────────────────

    def _plan_quota(self, db: Session, user_id: int, quota_type: QuotaType) -> int:
        plan = self.get_user_plan(db, user_id)
        if plan is None:
            return 0
        return {
            "consultation": plan.quota_consultation,
            "review": plan.quota_review,
            "draft": plan.quota_draft,
        }.get(quota_type, 0)

    def _get_or_create_usage_atomic(self, db: Session, user_id: int, month: str) -> QuotaUsage:
        """按 UNIQUE(user, month) 原子获取或创建用量行（并发安全）。"""
        usage = db.query(QuotaUsage).filter(
            QuotaUsage.user_id == user_id,
            QuotaUsage.year_month == month,
        ).first()
        if usage is not None:
            return usage
        usage = QuotaUsage(user_id=user_id, year_month=month)
        db.add(usage)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            usage = db.query(QuotaUsage).filter(
                QuotaUsage.user_id == user_id,
                QuotaUsage.year_month == month,
            ).first()
            if usage is None:
                raise
        return usage

    def reserve_quota(self, *, db: Session, user_id: int, quota_type: QuotaType,
                      usage_event_id: str, quantity: int = 1,
                      source_type: str | None = None, source_id: str | None = None,
                      ttl_seconds: int | None = None) -> UsageReservation:
        """原子预留配额：条件 UPDATE 扣减（不超额），返回预留记录。

        - 重复 usage_event_id 返回已有预留（幂等）。
        - 超配额：置 released 并抛 QuotaExceededError（QUOTA_EXCEEDED）。
        """
        if quantity <= 0:
            raise ValueError("配额数量必须为正")
        month = self._current_month()
        usage = self._get_or_create_usage_atomic(db, user_id, month)
        limit = self._plan_quota(db, user_id, quota_type)
        user = db.query(User).filter(User.id == user_id).first()

        reservation = UsageReservation(
            user_id=user_id,
            tenant_id=user.organization_id if user else None,
            quota_type=quota_type,
            quantity=quantity,
            status="reserved",
            usage_event_id=usage_event_id,
            billing_period=month,
            source_type=source_type,
            source_id=source_id,
            expires_at=(utc_now() + timedelta(seconds=ttl_seconds)) if ttl_seconds else None,
        )
        db.add(reservation)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            existing = db.query(UsageReservation).filter(
                UsageReservation.usage_event_id == usage_event_id).first()
            if existing is None:
                raise
            return existing

        col = getattr(QuotaUsage, f"{quota_type}_count")
        if limit == -1:
            db.query(QuotaUsage).filter(QuotaUsage.id == usage.id).update(
                {col: col + quantity}, synchronize_session=False)
            db.commit()
            return reservation

        rowcount = db.query(QuotaUsage).filter(
            QuotaUsage.id == usage.id,
            col + quantity <= limit,
        ).update({col: col + quantity}, synchronize_session=False)
        if rowcount == 1:
            db.commit()
            return reservation
        # 超配额：释放预留
        reservation.status = "released"
        db.commit()
        raise QuotaExceededError()

    def commit_usage(self, *, db: Session, usage_event_id: str) -> UsageReservation | None:
        """预留 → committed（幂等：仅 reserved 可提交；已 committed 直接返回）。"""
        reservation = db.query(UsageReservation).filter(
            UsageReservation.usage_event_id == usage_event_id).first()
        if reservation is None:
            return None
        if reservation.status == "committed":
            return reservation
        if reservation.status != "reserved":
            return reservation
        reservation_transition(reservation.status, "committed")
        reservation.status = "committed"
        db.commit()
        return reservation

    def _rollback_quota_and_transition(self, *, db: Session, reservation: UsageReservation,
                                       target_status: str) -> None:
        """回滚已扣减配额并把预留从 reserved 迁移到目标状态。

        不在此处 commit，由调用方统一提交；仅允许从 reserved 出发的迁移。
        """
        month = reservation.billing_period
        usage = db.query(QuotaUsage).filter(
            QuotaUsage.user_id == reservation.user_id,
            QuotaUsage.year_month == month,
        ).first()
        if usage is not None:
            # 回滚扣减，地板 0，防止超额释放
            from sqlalchemy import text as sa_text

            db.execute(sa_text(
                f"UPDATE quota_usages SET {reservation.quota_type}_count = "
                f"MAX(0, {reservation.quota_type}_count - :q) WHERE id = :id"
            ), {"q": reservation.quantity, "id": usage.id})
        reservation_transition(reservation.status, target_status)
        reservation.status = target_status

    def release_usage(self, *, db: Session, usage_event_id: str) -> UsageReservation | None:
        """预留 → released（失败/取消回滚已扣减的配额，幂等）。"""
        reservation = db.query(UsageReservation).filter(
            UsageReservation.usage_event_id == usage_event_id).first()
        if reservation is None or reservation.status != "reserved":
            return reservation
        self._rollback_quota_and_transition(db=db, reservation=reservation, target_status="released")
        db.commit()
        return reservation

    def expire_stale_reservations(self, *, db: Session, limit: int = 200) -> int:
        """清理过期预留（reserved 且超过 expires_at → expired + 回滚配额）。"""
        now = utc_now()
        stale = db.query(UsageReservation).filter(
            UsageReservation.status == "reserved",
            UsageReservation.expires_at.isnot(None),
            UsageReservation.expires_at < now,
        ).limit(limit).all()
        count = 0
        for reservation in stale:
            # 直接 reserved → expired 一次性迁移并回滚配额。
            # 不能先 release_usage 再转 expired：released 在状态机中没有出边，会抛 BillingStateError。
            self._rollback_quota_and_transition(db=db, reservation=reservation, target_status="expired")
            count += 1
        if stale:
            db.commit()
        return count

    def try_consume_quota(self, *, db: Session, user_id: int, quota_type: QuotaType,
                          usage_event_id: str, quantity: int = 1,
                          source_type: str | None = None, source_id: str | None = None) -> dict:
        """同步路径原子消费：reserve + 立即 commit。返回 {"ok": bool, "error_code": str}。"""
        try:
            reservation = self.reserve_quota(
                db=db, user_id=user_id, quota_type=quota_type,
                usage_event_id=usage_event_id, quantity=quantity,
                source_type=source_type, source_id=source_id)
            self.commit_usage(db=db, usage_event_id=reservation.usage_event_id)
            return {"ok": True, "error_code": "ok"}
        except QuotaExceededError:
            return {"ok": False, "error_code": "QUOTA_EXCEEDED"}

    # ── 兼容旧 API（顺序 check → record，仅新增调用不使用）─────────────────

    def get_or_create_usage(self, db: Session, user_id: int) -> QuotaUsage:
        """获取或创建当月用量记录（兼容旧 API）。"""
        month = self._current_month()
        usage = self._get_or_create_usage_atomic(db, user_id, month)
        db.commit()
        return usage

    def check_quota(self, db: Session, user_id: int, quota_type: QuotaType) -> bool:
        """检查用户是否还有配额（兼容旧 API）。返回 True 表示可以继续。"""
        plan = self.get_user_plan(db, user_id)
        if plan is None:
            return False
        quota_field = {
            "consultation": plan.quota_consultation,
            "review": plan.quota_review,
            "draft": plan.quota_draft,
        }.get(quota_type)
        if quota_field is None:
            return False
        if quota_field == -1:
            return True
        usage = self.get_or_create_usage(db, user_id)
        used = {
            "consultation": usage.consultation_count,
            "review": usage.review_count,
            "draft": usage.draft_count,
        }.get(quota_type, 0)
        return used < quota_field

    def record_usage(self, db: Session, user_id: int, quota_type: QuotaType) -> QuotaUsage:
        """记录一次使用（兼容旧 API）。"""
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

    # ── 订阅激活 / 取消 / 过期 ─────────────────────────────────────

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
        idempotency_key: str | None = None,
        reason: str = "payment_confirmed",
    ) -> UserSubscription:
        """支付确认后激活订阅（取消旧订阅；幂等键去重；套餐版本快照）。"""
        if idempotency_key:
            existing = db.query(UserSubscription).filter(
                UserSubscription.idempotency_key == idempotency_key).first()
            if existing is not None:
                return existing

        # 取消现有活跃订阅（经状态机，记录原因）；同幂等键的旧记录不重复取消
        active_subs = db.query(UserSubscription).filter(
            UserSubscription.user_id == user_id,
            UserSubscription.status.in_([SubscriptionStatus.active.value,
                                         SubscriptionStatus.past_due.value,
                                         SubscriptionStatus.suspended.value]),
        ).all()
        for sub in active_subs:
            if idempotency_key and sub.idempotency_key == idempotency_key:
                continue
            self.transition_subscription(db=db, sub=sub, to=SUB_CANCELLED,
                                         reason=reason, actor_id=user_id)

        plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.tier == plan_tier).first()
        if not plan:
            raise ValueError(f"Unknown plan tier: {plan_tier}")
        self.snapshot_plan(db, plan)

        sub = UserSubscription(
            user_id=user_id,
            plan_id=plan.id,
            plan_version=plan.price_version,
            status=SubscriptionStatus.active.value,
            payment_provider=payment_provider,
            payment_subscription_id=payment_subscription_id,
            payment_customer_id=payment_customer_id,
            current_period_start=period_start,
            current_period_end=period_end,
            idempotency_key=idempotency_key,
        )
        db.add(sub)
        db.commit()
        db.refresh(sub)
        oplog_service.log(module="subscription", action="subscription_activated", db=db,
                          user_id=user_id, target_type="user_subscription", target_id=sub.id,
                          detail=f"tier={plan_tier}; plan_version={plan.price_version}")
        return sub

    def cancel_subscription(self, db: Session, user_id: int,
                            reason: str = "user_cancelled") -> Optional[UserSubscription]:
        """取消用户订阅（降回免费），经状态机。"""
        sub = self.get_active_subscription(db, user_id)
        if not sub:
            return None
        self.transition_subscription(db=db, sub=sub, to=SUB_CANCELLED,
                                     reason=reason, actor_id=user_id)
        sub.cancelled_at = utc_now()
        db.add(sub)
        db.commit()
        db.refresh(sub)
        return sub

    def cancel_by_provider_id(self, db: Session, subscription_id: str,
                              reason: str = "provider_cancelled") -> Optional[UserSubscription]:
        """按供应商订阅 ID 取消（webhook 用）。"""
        sub = db.query(UserSubscription).filter(
            UserSubscription.payment_subscription_id == subscription_id).first()
        if not sub:
            return None
        if sub.status in (SubscriptionStatus.cancelled.value, SubscriptionStatus.expired.value):
            return sub
        self.transition_subscription(db=db, sub=sub, to=SUB_CANCELLED,
                                     reason=reason, actor_id=sub.user_id)
        sub.cancelled_at = utc_now()
        db.commit()
        db.refresh(sub)
        return sub

    def expire_overdue_subscriptions(self, db: Session) -> int:
        """周期任务：将已过 current_period_end 的 active 订阅置为 expired。"""
        now = utc_now()
        overdue = db.query(UserSubscription).filter(
            UserSubscription.status.in_([SubscriptionStatus.active.value,
                                         SubscriptionStatus.past_due.value,
                                         SubscriptionStatus.suspended.value]),
            UserSubscription.current_period_end.isnot(None),
            UserSubscription.current_period_end < now,
        ).all()
        count = 0
        for sub in overdue:
            self.transition_subscription(db=db, sub=sub, to=SUB_EXPIRED,
                                         reason="period_ended", actor_id=sub.user_id)
            sub.cancelled_at = sub.cancelled_at or now
            count += 1
        if overdue:
            db.commit()
        return count

    # ── 计划种子与版本维护 ─────────────────────────────────────────

    def ensure_default_plans(self, db: Session) -> None:
        """确保默认计划存在（应用启动时调用）。

        free 档位配额从 settings 读取；配额变化时递增 price_version 并生成新快照，
        不覆盖历史订阅的授时权益。
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
                plan = SubscriptionPlan(**d)
                db.add(plan)
                db.flush()
                self.snapshot_plan(db, plan)
            elif self._plan_requires_new_version(plan, d):
                # 配额/价格变化 → 新版本快照，不原地覆盖历史权益
                plan.description = d["description"]
                plan.quota_consultation = d["quota_consultation"]
                plan.quota_review = d["quota_review"]
                plan.quota_draft = d["quota_draft"]
                plan.price_version = (plan.price_version or 1) + 1
                db.flush()
                self.snapshot_plan(db, plan)
        db.commit()

    @staticmethod
    def _plan_requires_new_version(plan: SubscriptionPlan, d: dict) -> bool:
        return (plan.quota_consultation != d["quota_consultation"]
                or plan.quota_review != d["quota_review"]
                or plan.quota_draft != d["quota_draft"]
                or Decimal(str(plan.price_monthly)) != Decimal(str(d["price_monthly"])))


subscription_service = SubscriptionService()
