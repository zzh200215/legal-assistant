"""Phase 10 Week 2 tests: subscription plans, quota management, payment webhook"""
from datetime import datetime
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import create_access_token, hash_password
from app.core.database import Base, get_db
from app.main import app
from app.models.subscription import SubscriptionPlan, UserSubscription, SubscriptionStatus
from app.models.operation_log import OperationLog
from app.models.user import User, UserStatus
from app.services.billing.subscription_service import subscription_service


class SubscriptionServiceTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        self.db = self.SessionLocal()

        self.user = User(
            username="sub_user",
            email="sub@test.com",
            hashed_password=hash_password("pw"),
            role="user",
            status=UserStatus.active.value,
        )
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

        subscription_service.ensure_default_plans(self.db)

    def tearDown(self):
        self.db.close()

    # ── 计划 ──

    def test_ensure_default_plans_creates_three(self):
        plans = self.db.query(SubscriptionPlan).all()
        tiers = {p.tier for p in plans}
        self.assertEqual(tiers, {"free", "pro", "team"})

    def test_ensure_default_plans_idempotent(self):
        """重复调用不应创建重复计划"""
        subscription_service.ensure_default_plans(self.db)
        subscription_service.ensure_default_plans(self.db)
        count = self.db.query(SubscriptionPlan).count()
        self.assertEqual(count, 3)

    def test_no_subscription_defaults_to_free(self):
        plan = subscription_service.get_user_plan(self.db, self.user.id)
        self.assertEqual(plan.tier, "free")

    # ── 配额检查 ──

    def test_free_plan_quota_allows_within_limit(self):
        """免费版：首次咨询应允许"""
        allowed = subscription_service.check_quota(self.db, self.user.id, "consultation")
        self.assertTrue(allowed)

    def test_free_plan_quota_exhausted(self):
        """免费版：超过5次咨询后应被拒"""
        usage = subscription_service.get_or_create_usage(self.db, self.user.id)
        usage.consultation_count = 5
        self.db.add(usage)
        self.db.commit()

        allowed = subscription_service.check_quota(self.db, self.user.id, "consultation")
        self.assertFalse(allowed)

    def test_pro_plan_quota(self):
        """专业版：50次内都允许"""
        pro_plan = self.db.query(SubscriptionPlan).filter(SubscriptionPlan.tier == "pro").first()
        self.db.add(UserSubscription(
            user_id=self.user.id,
            plan_id=pro_plan.id,
            status=SubscriptionStatus.active.value,
        ))
        self.db.commit()

        usage = subscription_service.get_or_create_usage(self.db, self.user.id)
        usage.consultation_count = 49
        self.db.add(usage)
        self.db.commit()

        self.assertTrue(subscription_service.check_quota(self.db, self.user.id, "consultation"))

    def test_pro_plan_quota_exhausted(self):
        pro_plan = self.db.query(SubscriptionPlan).filter(SubscriptionPlan.tier == "pro").first()
        self.db.add(UserSubscription(
            user_id=self.user.id, plan_id=pro_plan.id,
            status=SubscriptionStatus.active.value,
        ))
        self.db.commit()

        usage = subscription_service.get_or_create_usage(self.db, self.user.id)
        usage.consultation_count = 50
        self.db.add(usage)
        self.db.commit()

        self.assertFalse(subscription_service.check_quota(self.db, self.user.id, "consultation"))

    def test_team_plan_has_fixed_quota(self):
        """团队版：合同化固定上限（M-1，不再是无限），超限即拒绝"""
        team_plan = self.db.query(SubscriptionPlan).filter(SubscriptionPlan.tier == "team").first()
        self.db.add(UserSubscription(
            user_id=self.user.id, plan_id=team_plan.id,
            status=SubscriptionStatus.active.value,
        ))
        self.db.commit()

        self.assertEqual(team_plan.quota_consultation, 5000)

        # 未达上限：允许
        usage = subscription_service.get_or_create_usage(self.db, self.user.id)
        usage.consultation_count = 100
        self.db.add(usage)
        self.db.commit()
        self.assertTrue(subscription_service.check_quota(self.db, self.user.id, "consultation"))

        # 超过上限：拒绝（不再是 -1 无限）
        usage.consultation_count = 6000
        self.db.add(usage)
        self.db.commit()
        self.assertFalse(subscription_service.check_quota(self.db, self.user.id, "consultation"))

    # ── 用量记录 ──

    def test_record_usage_increments(self):
        subscription_service.record_usage(self.db, self.user.id, "consultation")
        subscription_service.record_usage(self.db, self.user.id, "consultation")
        subscription_service.record_usage(self.db, self.user.id, "review")

        usage = subscription_service.get_or_create_usage(self.db, self.user.id)
        self.assertEqual(usage.consultation_count, 2)
        self.assertEqual(usage.review_count, 1)
        self.assertEqual(usage.draft_count, 0)

    def test_usage_summary_format(self):
        summary = subscription_service.get_usage_summary(self.db, self.user.id)
        self.assertIn("year_month", summary)
        self.assertIn("plan_tier", summary)
        self.assertIn("consultation", summary)
        self.assertEqual(summary["consultation"]["quota"], 5)
        self.assertEqual(summary["consultation"]["used"], 0)

    # ── M-3 免费档参数化（B 组 8/3/3） ──

    def test_free_plan_quota_parameterized_via_settings(self):
        """M-3：FREE_PLAN_*_QUOTA 配置应同步到 free 计划配额与描述，无需迁移。"""
        from app.core.config import get_settings
        with patch.object(get_settings(), "FREE_PLAN_CONSULTATION_QUOTA", 8), \
             patch.object(get_settings(), "FREE_PLAN_REVIEW_QUOTA", 3), \
             patch.object(get_settings(), "FREE_PLAN_DRAFT_QUOTA", 3):
            subscription_service.ensure_default_plans(self.db)

        free_plan = self.db.query(SubscriptionPlan).filter(SubscriptionPlan.tier == "free").first()
        self.assertEqual(free_plan.quota_consultation, 8)
        self.assertEqual(free_plan.quota_review, 3)
        self.assertEqual(free_plan.quota_draft, 3)
        self.assertIn("咨询8次", free_plan.description)

        # pro/team 不受影响
        pro_plan = self.db.query(SubscriptionPlan).filter(SubscriptionPlan.tier == "pro").first()
        self.assertEqual(pro_plan.quota_consultation, 50)
        self.assertEqual(subscription_service.get_usage_summary(self.db, self.user.id)["consultation"]["quota"], 8)

    # ── 激活订阅 ──

    def test_activate_subscription_replaces_old(self):
        """激活新订阅时旧订阅自动取消"""
        pro_plan = self.db.query(SubscriptionPlan).filter(SubscriptionPlan.tier == "pro").first()
        # 先激活 pro
        sub1 = UserSubscription(
            user_id=self.user.id, plan_id=pro_plan.id,
            status=SubscriptionStatus.active.value,
        )
        self.db.add(sub1)
        self.db.commit()

        # 激活 team（应取消 pro）
        subscription_service.activate_subscription(
            self.db, self.user.id, "team",
            "stripe", "sub_team_001",
        )
        self.db.refresh(sub1)
        self.assertEqual(sub1.status, SubscriptionStatus.cancelled.value)

        active_sub = subscription_service.get_active_subscription(self.db, self.user.id)
        plan = self.db.query(SubscriptionPlan).filter(SubscriptionPlan.id == active_sub.plan_id).first()
        self.assertEqual(plan.tier, "team")

    def test_cancel_subscription(self):
        pro_plan = self.db.query(SubscriptionPlan).filter(SubscriptionPlan.tier == "pro").first()
        self.db.add(UserSubscription(
            user_id=self.user.id, plan_id=pro_plan.id,
            status=SubscriptionStatus.active.value,
        ))
        self.db.commit()

        sub = subscription_service.cancel_subscription(self.db, self.user.id)
        self.assertEqual(sub.status, SubscriptionStatus.cancelled.value)
        self.assertIsNone(subscription_service.get_active_subscription(self.db, self.user.id))

    # ── 过期流转（P1 状态机补全：active→expired）──

    def test_expire_overdue_subscriptions(self):
        """已过 current_period_end 的 active 订阅置为 expired，未过期不受影响。"""
        from datetime import timedelta, timezone

        pro_plan = self.db.query(SubscriptionPlan).filter(SubscriptionPlan.tier == "pro").first()
        now = datetime.now(timezone.utc)

        # 已过期订阅（用户1）
        self.db.add(UserSubscription(
            user_id=self.user.id, plan_id=pro_plan.id,
            status=SubscriptionStatus.active.value,
            current_period_start=now - timedelta(days=40),
            current_period_end=now - timedelta(days=10),
        ))
        # 未过期订阅（用户2）
        user2 = User(
            username="sub_user2", email="sub2@test.com",
            hashed_password=hash_password("pw"), role="user",
            status=UserStatus.active.value,
        )
        self.db.add(user2)
        self.db.commit()
        self.db.refresh(user2)
        self.db.add(UserSubscription(
            user_id=user2.id, plan_id=pro_plan.id,
            status=SubscriptionStatus.active.value,
            current_period_start=now - timedelta(days=5),
            current_period_end=now + timedelta(days=25),
        ))
        self.db.commit()

        n = subscription_service.expire_overdue_subscriptions(self.db)
        self.assertEqual(n, 1)

        # 过期用户：无活跃订阅，配额回落到 free
        self.assertIsNone(subscription_service.get_active_subscription(self.db, self.user.id))
        self.assertEqual(subscription_service.get_user_plan(self.db, self.user.id).tier, "free")
        # 未过期用户：保持 pro
        self.assertIsNotNone(subscription_service.get_active_subscription(self.db, user2.id))
        self.assertEqual(subscription_service.get_user_plan(self.db, user2.id).tier, "pro")

    def test_expire_ignores_cancelled_and_pending(self):
        """cancelled/pending 订阅即使已过周期也不应被误置 expired。"""
        from datetime import timedelta, timezone

        pro_plan = self.db.query(SubscriptionPlan).filter(SubscriptionPlan.tier == "pro").first()
        now = datetime.now(timezone.utc)
        for status in (SubscriptionStatus.cancelled.value, SubscriptionStatus.pending.value):
            self.db.add(UserSubscription(
                user_id=self.user.id, plan_id=pro_plan.id,
                status=status,
                current_period_end=now - timedelta(days=1),
            ))
        self.db.commit()

        n = subscription_service.expire_overdue_subscriptions(self.db)
        self.assertEqual(n, 0)
        statuses = {s.status for s in self.db.query(UserSubscription).all()}
        self.assertEqual(statuses, {SubscriptionStatus.cancelled.value, SubscriptionStatus.pending.value})


class SubscriptionApiTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        self.db = self.SessionLocal()

        self.user = User(
            username="api_user",
            email="api@test.com",
            hashed_password=hash_password("pw"),
            role="user",
            status=UserStatus.active.value,
        )
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)
        self.token = create_access_token({"sub": self.user.id, "role": "user"})
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def tearDown(self):
        self.db.close()
        app.dependency_overrides.clear()

    def test_list_plans(self):
        """GET /api/billing/plans 返回三个计划"""
        resp = self.client.get("/api/billing/plans")
        self.assertEqual(resp.status_code, 200)
        plans = resp.json()["data"]
        self.assertEqual(len(plans), 3)
        tiers = {p["tier"] for p in plans}
        self.assertEqual(tiers, {"free", "pro", "team"})

    def test_get_my_subscription_free(self):
        """未订阅用户使用免费计划"""
        resp = self.client.get("/api/billing/subscriptions/me", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(data["plan"]["tier"], "free")
        self.assertIsNone(data["subscription"])

    def test_get_quota_initial(self):
        """新用户配额全为0"""
        resp = self.client.get("/api/billing/subscriptions/quota", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(data["consultation"]["used"], 0)
        self.assertEqual(data["consultation"]["quota"], 5)

    def test_checkout_creates_session(self):
        """创建 checkout 会话"""
        resp = self.client.post("/api/billing/subscriptions/checkout?tier=pro", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertIn("checkout_url", data)
        self.assertEqual(data["tier"], "pro")

    def test_checkout_invalid_tier(self):
        resp = self.client.post("/api/billing/subscriptions/checkout?tier=enterprise", headers=self.headers)
        self.assertEqual(resp.status_code, 400)

    def _signed_post(self, payload: dict, secret: str = "whsec_test_secret",
                     signature: str | None = "auto"):
        """POST webhook：生成有效/自定义签名。"""
        import hashlib
        import hmac
        import json
        import time as _time

        raw = json.dumps(payload).encode("utf-8")
        if signature == "auto":
            ts = str(int(_time.time()))
            sig = hmac.new(secret.encode("utf-8"), f"{ts}.{raw.decode('utf-8')}".encode("utf-8"),
                           hashlib.sha256).hexdigest()
            header = f"t={ts},v1={sig}"
        elif signature is None:
            header = None
        else:
            header = signature
        headers = {"Content-Type": "application/json"}
        if header:
            headers["x-stripe-signature"] = header
        return self.client.post("/api/billing/subscriptions/webhook", content=raw, headers=headers)

    def _dispatch_webhooks(self):
        from app.tasks import dispatch_payment_events_task
        with patch("app.tasks.billing_tasks.SessionLocal", self.SessionLocal):
            return dispatch_payment_events_task()

    def test_webhook_requires_valid_signature_when_configured(self):
        """#82/配置 PAYMENT_WEBHOOK_SECRET 后必须验签；有效签名登记事件，伪造签名拒绝"""
        from app.core.config import get_settings

        payload = {"provider": "stripe", "event_type": "customer.subscription.created",
                   "data": {"object": {"metadata": {"user_id": self.user.id, "plan_tier": "pro"},
                                       "id": "sub_test", "customer": "cus_test"}}}
        with patch.object(get_settings(), "PAYMENT_WEBHOOK_SECRET", "whsec_test_secret"):
            # 有效签名
            resp = self._signed_post(payload)
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertTrue(resp.json()["success"])
            # 伪造签名
            import hashlib
            import hmac
            import json
            import time as _time
            raw = json.dumps(payload).encode("utf-8")
            ts = str(int(_time.time()))
            bad = hmac.new(b"wrong_secret", f"{ts}.{raw.decode('utf-8')}".encode("utf-8"), hashlib.sha256).hexdigest()
            resp = self._signed_post(payload, signature=f"t={ts},v1={bad}")
            self.assertEqual(resp.status_code, 400, resp.text)
            self.assertIn("签名", resp.text)
            # 缺少签名头
            resp = self._signed_post(payload, signature=None)
            self.assertEqual(resp.status_code, 400, resp.text)

    def test_webhook_rejected_when_no_secret_configured(self):
        """fail-closed：未配置 PAYMENT_WEBHOOK_SECRET 时拒绝事件，不产生副作用"""
        from app.core.config import get_settings

        with patch.object(get_settings(), "PAYMENT_WEBHOOK_SECRET", ""):
            payload = {"provider": "stripe", "event_type": "customer.subscription.created",
                       "data": {"object": {"metadata": {"user_id": self.user.id, "plan_tier": "pro"},
                                           "id": "sub_ok", "customer": "cus_ok"}}}
            resp = self._signed_post(payload)
            self.assertEqual(resp.status_code, 400, resp.text)
            self.assertIn("WEBHOOK_SIGNATURE_NOT_CONFIGURED", resp.text)
        # 不产生副作用：无订阅被激活
        self.assertIsNone(subscription_service.get_active_subscription(self.db, self.user.id))

    def test_checkout_records_upgrade_intent_oplog(self):
        """#81/升级意图埋点：checkout 调用写 operation_logs (upgrade_intent)"""
        resp = self.client.post("/api/billing/subscriptions/checkout?tier=team", headers=self.headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        log = (
            self.db.query(OperationLog)
            .filter(OperationLog.module == "subscription", OperationLog.action == "upgrade_intent")
            .first()
        )
        self.assertIsNotNone(log, "checkout 应记录 upgrade_intent 操作日志")
        self.assertEqual(log.user_id, self.user.id)
        self.assertIn("tier=team", log.detail)

    def test_webhook_stripe_activate(self):
        """Stripe webhook：验签 → 落库 → 异步任务激活订阅"""
        from app.core.config import get_settings

        subscription_service.ensure_default_plans(self.db)
        payload = {
            "provider": "stripe",
            "event_type": "customer.subscription.created",
            "data": {
                "object": {
                    "id": "sub_stripe_001",
                    "customer": "cus_001",
                    "metadata": {"user_id": str(self.user.id), "plan_tier": "pro"},
                }
            },
        }
        with patch.object(get_settings(), "PAYMENT_WEBHOOK_SECRET", "whsec_test_secret"):
            resp = self._signed_post(payload)
        self.assertEqual(resp.status_code, 200, resp.text)
        # 事件已落库但未同步激活（异步）
        from app.models.payment_event import PaymentEvent
        event = self.db.query(PaymentEvent).filter(
            PaymentEvent.provider_event_id == "sub_stripe_001").first()
        self.assertIsNotNone(event)
        self.assertEqual(event.status, "pending")
        # 异步任务处理 → 激活订阅
        self._dispatch_webhooks()
        sub = subscription_service.get_active_subscription(self.db, self.user.id)
        self.assertIsNotNone(sub)
        plan = self.db.query(SubscriptionPlan).filter(SubscriptionPlan.id == sub.plan_id).first()
        self.assertEqual(plan.tier, "pro")
        self.db.refresh(event)
        self.assertEqual(event.status, "completed")

    def test_webhook_pingpp_activate(self):
        """Ping++ webhook：异步任务激活订阅（team）"""
        from app.core.config import get_settings

        subscription_service.ensure_default_plans(self.db)
        payload = {
            "provider": "pingpp",
            "event_type": "charge.succeeded",
            "data": {
                "object": {
                    "id": "ch_pingpp_001",
                    "metadata": {"user_id": str(self.user.id), "plan_tier": "team"},
                }
            },
        }
        with patch.object(get_settings(), "PAYMENT_WEBHOOK_SECRET", "whsec_test_secret"):
            resp = self._signed_post(payload)
        self.assertEqual(resp.status_code, 200, resp.text)
        self._dispatch_webhooks()
        sub = subscription_service.get_active_subscription(self.db, self.user.id)
        self.assertIsNotNone(sub)
        plan = self.db.query(SubscriptionPlan).filter(SubscriptionPlan.id == sub.plan_id).first()
        self.assertEqual(plan.tier, "team")

    def test_cancel_subscription_api(self):
        """取消订阅 API"""
        subscription_service.ensure_default_plans(self.db)
        pro_plan = self.db.query(SubscriptionPlan).filter(SubscriptionPlan.tier == "pro").first()
        self.db.add(UserSubscription(
            user_id=self.user.id, plan_id=pro_plan.id,
            status=SubscriptionStatus.active.value,
        ))
        self.db.commit()

        resp = self.client.post("/api/billing/subscriptions/cancel", headers=self.headers)
        self.assertEqual(resp.status_code, 200)

        self.assertIsNone(subscription_service.get_active_subscription(self.db, self.user.id))

    def test_cancel_no_subscription(self):
        resp = self.client.post("/api/billing/subscriptions/cancel", headers=self.headers)
        self.assertEqual(resp.status_code, 404)


class QuotaEnforcementTests(unittest.TestCase):
    """测试法律API配额超限时返回429"""

    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        self.db = self.SessionLocal()

        self.user = User(
            username="quota_user",
            email="quota@test.com",
            hashed_password=hash_password("pw"),
            role="user",
            status=UserStatus.active.value,
        )
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

        subscription_service.ensure_default_plans(self.db)

        # 耗尽所有配额
        usage = subscription_service.get_or_create_usage(self.db, self.user.id)
        usage.consultation_count = 5
        usage.review_count = 2
        usage.draft_count = 2
        self.db.add(usage)
        self.db.commit()

        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)
        self.token = create_access_token({"sub": self.user.id, "role": "user"})
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def tearDown(self):
        self.db.close()
        app.dependency_overrides.clear()

    def test_consultation_quota_exceeded_returns_429(self):
        resp = self.client.post("/api/legal/consultations", json={"question": "合同问题"}, headers=self.headers)
        self.assertEqual(resp.status_code, 429)

    def test_review_quota_exceeded_returns_429(self):
        resp = self.client.post("/api/legal/contract-reviews", json={
            "title": "测试合同",
            "content": "乙方应在合同签订后30日内付款。"
        }, headers=self.headers)
        self.assertEqual(resp.status_code, 429)

    def test_draft_quota_exceeded_returns_429(self):
        resp = self.client.post("/api/legal/drafts", json={
            "document_type": "labor_contract",
            "fields": {},
        }, headers=self.headers)
        self.assertEqual(resp.status_code, 429)


if __name__ == "__main__":
    unittest.main()
