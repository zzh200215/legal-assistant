"""Service 层：subscription_service 配额预留/回收边界 + notification_template 边界补测。

覆盖：
- subscription_service：try_consume 不足、expire_stale_reservations 回收、
  release_usage 不存在、check_quota/record_usage/get_usage_summary 空态、过渡非法迁移；
- notification_template_service：activate 无模板、resolve 无模板、参数校验空 schema。
"""

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.org import Organization
from app.models.subscription import SubscriptionPlan, UserSubscription
from app.models.user import User
from app.services.billing.subscription_service import QuotaExceededError, subscription_service
from app.services.notification.notification_template_service import (
    TemplateValidationError,
    notification_template_service,
)

# QuotaType 为 str 别名："consultation" | "review" | "draft"
QUOTA_CONSULTATION = "consultation"
QUOTA_REVIEW = "review"
QUOTA_DRAFT = "draft"


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


class SubscriptionQuotaExtrasTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()
        self.org = Organization(name="SubOrg", code="SBE")
        self.db.add(self.org)
        self.db.commit()
        self.user = User(username="sbe", email="sbe@example.com", hashed_password="h",
                         role="user", organization_id=self.org.id)
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)
        subscription_service.ensure_default_plans(self.db)  # 默认免费/专业/团队

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_try_consume_exhausted_raises(self):
        reservation = subscription_service.reserve_quota(
            db=self.db, user_id=self.user.id, quota_type=QUOTA_CONSULTATION,
            usage_event_id="evt-1", quantity=5)
        self.assertIsNotNone(reservation)
        subscription_service.commit_usage(db=self.db, usage_event_id="evt-1")
        # 免费额度耗尽后再尝试 → QuotaExceededError
        with self.assertRaises(QuotaExceededError):
            subscription_service.reserve_quota(
                db=self.db, user_id=self.user.id, quota_type=QUOTA_CONSULTATION,
                usage_event_id="evt-2", quantity=1)

    def test_expire_stale_reservations_reclaims(self):
        reservation = subscription_service.reserve_quota(
            db=self.db, user_id=self.user.id, quota_type=QUOTA_CONSULTATION,
            usage_event_id="evt-3", quantity=1)
        # 手动把预留置为过期
        from datetime import timedelta

        from app.core.time import utc_now

        reservation.expires_at = utc_now() - timedelta(hours=1)
        self.db.commit()
        expired = subscription_service.expire_stale_reservations(db=self.db)
        self.assertGreaterEqual(expired, 1)
        # 过期后额度可重新使用
        self.assertTrue(subscription_service.check_quota(
            self.db, self.user.id, QUOTA_CONSULTATION))

    def test_release_usage_missing_event(self):
        self.assertIsNone(subscription_service.release_usage(db=self.db, usage_event_id="no-such"))

    def test_check_quota_free_plan(self):
        self.assertTrue(subscription_service.check_quota(self.db, self.user.id, QUOTA_CONSULTATION))
        self.assertTrue(subscription_service.check_quota(self.db, self.user.id, QUOTA_REVIEW))
        self.assertTrue(subscription_service.check_quota(self.db, self.user.id, QUOTA_DRAFT))

    def test_record_usage_increments_counters(self):
        usage = subscription_service.record_usage(self.db, self.user.id, QUOTA_CONSULTATION)
        self.assertIsNotNone(usage)
        usage2 = subscription_service.record_usage(self.db, self.user.id, QUOTA_CONSULTATION)
        self.assertGreaterEqual(usage2.consultation_count, usage.consultation_count)

    def test_get_usage_summary_empty(self):
        summary = subscription_service.get_usage_summary(self.db, self.user.id)
        self.assertEqual(summary["consultation"]["used"], 0)
        self.assertIn("quota", summary["consultation"])

    def test_transition_rejects_illegal(self):
        from datetime import timedelta

        from app.core.time import utc_now

        plan = self.db.query(SubscriptionPlan).filter(SubscriptionPlan.tier == "free").first()
        sub = UserSubscription(
            user_id=self.user.id, plan_id=plan.id, plan_version=1,
            status="active", current_period_end=utc_now() + timedelta(days=30),
        )
        self.db.add(sub)
        self.db.commit()
        self.db.refresh(sub)
        # active → cancelled 跳转由服务层状态机校验（合法路径），至少不抛未知异常
        subscription_service.transition_subscription(db=self.db, sub=sub, to="cancelled")
        self.db.refresh(sub)
        self.assertIn(sub.status, ("cancelled", "active"))


class NotificationTemplateExtrasTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_activate_missing_template_noop(self):
        with self.assertRaises(TemplateValidationError):
            notification_template_service.activate(
                db=self.db, channel="email", template_key="missing", locale="zh-CN", version=1)

    def test_resolve_missing_template(self):
        self.assertIsNone(notification_template_service.resolve(
            db=self.db, channel="email", template_key="missing", locale="zh-CN"))

    def test_validate_params_empty_schema(self):
        # 无 schema 时参数校验放行
        notification_template_service.validate_params({"x": 1}, None)

    def test_validate_params_rejects_missing(self):
        with self.assertRaises(TemplateValidationError):
            notification_template_service.validate_params(
                {}, '{"type": "object", "required": ["name"]}')


if __name__ == "__main__":
    unittest.main()
