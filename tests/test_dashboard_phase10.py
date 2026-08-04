"""Phase 10 Week 3 tests: miniapp login + dashboard"""
import unittest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import create_access_token, hash_password
from app.core.database import Base, get_db
from app.main import app
from app.models.subscription import SubscriptionPlan, UserSubscription, QuotaUsage, SubscriptionStatus
from app.models.user import User, UserStatus, WechatUser
from app.services.subscription_service import subscription_service


class MiniAppLoginTests(unittest.TestCase):
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

        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        self.db.close()
        app.dependency_overrides.clear()

    def test_miniapp_login_not_configured(self):
        """未配置 WECHAT_APP_ID 时返回 500"""
        resp = self.client.post("/api/miniapp/login?js_code=testcode")
        self.assertEqual(resp.status_code, 500)

    def test_miniapp_login_creates_new_user(self):
        """首次小程序登录自动创建账号"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "openid": "mp_openid_001",
            "unionid": "mp_unionid_001",
            "session_key": "session_key",
        }

        with patch("app.api.miniapp_api.settings") as mock_settings:
            mock_settings.WECHAT_APP_ID = "test_mp_appid"
            mock_settings.WECHAT_APP_SECRET = "test_mp_secret"
            with patch("app.api.miniapp_api.requests.get", return_value=mock_resp):
                resp = self.client.post(
                    "/api/miniapp/login?js_code=code_001&nickname=小明用户"
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertIn("access_token", data)
        self.assertEqual(data["nickname"], "小明用户")

        # 验证数据库中创建了用户和绑定记录
        wx_user = self.db.query(WechatUser).filter(WechatUser.openid == "mp_openid_001").first()
        self.assertIsNotNone(wx_user)
        self.assertEqual(wx_user.nickname, "小明用户")

    def test_miniapp_login_existing_user(self):
        """已绑定用户直接返回 JWT"""
        existing_user = User(
            username="mp_existing", email="mp_exist@test.com",
            role="user", status=UserStatus.active.value,
        )
        self.db.add(existing_user)
        self.db.flush()
        self.db.add(WechatUser(
            user_id=existing_user.id,
            openid="mp_openid_existing",
            nickname="老用户",
        ))
        self.db.commit()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"openid": "mp_openid_existing"}

        with patch("app.api.miniapp_api.settings") as mock_settings:
            mock_settings.WECHAT_APP_ID = "test_mp_appid"
            mock_settings.WECHAT_APP_SECRET = "test_mp_secret"
            with patch("app.api.miniapp_api.requests.get", return_value=mock_resp):
                resp = self.client.post("/api/miniapp/login?js_code=code_existing")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertIn("access_token", data)

    def test_miniapp_login_wechat_error(self):
        """微信返回错误码时登录失败"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"errcode": 40029, "errmsg": "invalid code"}

        with patch("app.api.miniapp_api.settings") as mock_settings:
            mock_settings.WECHAT_APP_ID = "test_mp_appid"
            mock_settings.WECHAT_APP_SECRET = "test_mp_secret"
            with patch("app.api.miniapp_api.requests.get", return_value=mock_resp):
                resp = self.client.post("/api/miniapp/login?js_code=bad_code")

        self.assertEqual(resp.status_code, 401)

    def test_miniapp_quota_endpoint(self):
        """小程序查询配额"""
        user = User(
            username="mp_quota_user", email="mp_quota@test.com",
            hashed_password=hash_password("pw"), role="user",
            status=UserStatus.active.value,
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)

        token = create_access_token({"sub": user.id, "role": "user"})
        resp = self.client.get(
            "/api/miniapp/quota",
            headers={"Authorization": f"Bearer {token}"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertIn("consultation", data)


class DashboardTests(unittest.TestCase):
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

        # Admin user
        self.admin = User(
            username="dashboard_admin",
            email="dashboard_admin@test.com",
            hashed_password=hash_password("pw"),
            role="admin",
            status=UserStatus.active.value,
        )
        # Regular user
        self.regular = User(
            username="dashboard_regular",
            email="dashboard_regular@test.com",
            hashed_password=hash_password("pw"),
            role="user",
            status=UserStatus.active.value,
        )
        self.db.add_all([self.admin, self.regular])
        self.db.commit()
        self.db.refresh(self.admin)
        self.db.refresh(self.regular)

        subscription_service.ensure_default_plans(self.db)

        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)

        self.admin_token = create_access_token({"sub": self.admin.id, "role": "admin"})
        self.user_token = create_access_token({"sub": self.regular.id, "role": "user"})
        self.admin_headers = {"Authorization": f"Bearer {self.admin_token}"}
        self.user_headers = {"Authorization": f"Bearer {self.user_token}"}

    def tearDown(self):
        self.db.close()
        app.dependency_overrides.clear()

    # ── 权限控制 ──

    def test_dashboard_requires_admin(self):
        """非管理员访问仪表盘返回 403"""
        resp = self.client.get("/api/admin/dashboard", headers=self.user_headers)
        self.assertEqual(resp.status_code, 403)

    def test_dashboard_unauthenticated(self):
        """未认证返回 401"""
        resp = self.client.get("/api/admin/dashboard")
        self.assertEqual(resp.status_code, 401)

    # ── 仪表盘内容 ──

    def test_dashboard_returns_expected_keys(self):
        """仪表盘返回所有必要字段"""
        resp = self.client.get("/api/admin/dashboard", headers=self.admin_headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]

        self.assertIn("users", data)
        self.assertIn("subscriptions", data)
        self.assertIn("legal_stats", data)
        self.assertIn("daily_trend", data)
        self.assertIn("quota_warnings", data)

    def test_dashboard_user_counts(self):
        """仪表盘用户统计正确"""
        resp = self.client.get("/api/admin/dashboard", headers=self.admin_headers)
        data = resp.json()["data"]
        # admin + regular = 2
        self.assertEqual(data["users"]["total"], 2)
        self.assertEqual(data["users"]["active"], 2)

    def test_dashboard_subscription_distribution(self):
        """未订阅用户计入免费版"""
        resp = self.client.get("/api/admin/dashboard", headers=self.admin_headers)
        data = resp.json()["data"]
        # 没有活跃订阅，2个用户都是免费版
        self.assertEqual(data["subscriptions"]["free"], 2)
        self.assertEqual(data["subscriptions"]["pro"], 0)
        self.assertEqual(data["subscriptions"]["team"], 0)

    def test_dashboard_subscription_with_paid(self):
        """有付费订阅时分布计数正确"""
        pro_plan = self.db.query(SubscriptionPlan).filter(SubscriptionPlan.tier == "pro").first()
        self.db.add(UserSubscription(
            user_id=self.regular.id,
            plan_id=pro_plan.id,
            status=SubscriptionStatus.active.value,
        ))
        self.db.commit()

        resp = self.client.get("/api/admin/dashboard", headers=self.admin_headers)
        data = resp.json()["data"]
        self.assertEqual(data["subscriptions"]["pro"], 1)
        self.assertEqual(data["subscriptions"]["free"], 1)  # admin 还是免费

    def test_dashboard_daily_trend_7_days(self):
        """趋势数组包含最近7天"""
        resp = self.client.get("/api/admin/dashboard", headers=self.admin_headers)
        data = resp.json()["data"]
        self.assertEqual(len(data["daily_trend"]), 7)
        for entry in data["daily_trend"]:
            self.assertIn("date", entry)
            self.assertIn("consultations", entry)
            self.assertIn("reviews", entry)
            self.assertIn("drafts", entry)

    def test_dashboard_legal_stats_zero(self):
        """无法律业务时统计为0"""
        resp = self.client.get("/api/admin/dashboard", headers=self.admin_headers)
        data = resp.json()["data"]
        self.assertEqual(data["legal_stats"]["total"]["consultations"], 0)
        self.assertEqual(data["legal_stats"]["last_30d"]["reviews"], 0)

    # ── 用户统计端点 ──

    def test_users_stats_endpoint(self):
        from datetime import datetime, timedelta, timezone
        db = self.db
        before = db.query(User).count()
        now = datetime.now(timezone.utc)
        for i, name in enumerate(["s1", "s2"]):
            db.add(User(
                username=name, email=f"{name}@t.com",
                hashed_password=hash_password("pw"), role="user",
                status=UserStatus.active.value,
                created_at=now - timedelta(days=i),
            ))
        db.commit()
        resp = self.client.get("/api/admin/users-stats?days=7", headers=self.admin_headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)
        # 新增的 2 个用户都被按天统计到（其余存量用户若在窗口内也计入）
        self.assertGreaterEqual(sum(row["new_users"] for row in data), before + 2)
        for row in data:
            self.assertRegex(row["date"], r"^\d{4}-\d{2}-\d{2}$")

    def test_users_stats_requires_admin(self):
        resp = self.client.get("/api/admin/users-stats", headers=self.user_headers)
        self.assertEqual(resp.status_code, 403)

    # ── 收入估算端点 ──

    def test_revenue_endpoint_no_subscriptions(self):
        """无付费订阅时收入为0"""
        resp = self.client.get("/api/admin/subscription-revenue", headers=self.admin_headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(data["monthly_revenue_estimate"], 0.0)
        self.assertEqual(data["active_paid_subscriptions"], 0)

    def test_revenue_endpoint_with_subscriptions(self):
        """有订阅时收入计算正确"""
        pro_plan = self.db.query(SubscriptionPlan).filter(SubscriptionPlan.tier == "pro").first()
        team_plan = self.db.query(SubscriptionPlan).filter(SubscriptionPlan.tier == "team").first()

        self.db.add_all([
            UserSubscription(
                user_id=self.admin.id, plan_id=pro_plan.id,
                status=SubscriptionStatus.active.value,
            ),
            UserSubscription(
                user_id=self.regular.id, plan_id=team_plan.id,
                status=SubscriptionStatus.active.value,
            ),
        ])
        self.db.commit()

        resp = self.client.get("/api/admin/subscription-revenue", headers=self.admin_headers)
        data = resp.json()["data"]
        # 199 + 999 = 1198
        self.assertEqual(data["monthly_revenue_estimate"], 1198.0)
        self.assertEqual(data["active_paid_subscriptions"], 2)

    def test_revenue_requires_admin(self):
        resp = self.client.get("/api/admin/subscription-revenue", headers=self.user_headers)
        self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
