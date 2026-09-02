"""补齐企业认证 Provider：WeCom/DingTalk OAuth + LDAP 登录在演示模式（无凭据）下可端到端跑通。"""
import unittest
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from unittest.mock import patch

import app.models  # noqa: F401
from app.core.config import get_settings
from app.core.database import Base
from app.core.time import utc_now
from app.models.org import Organization
from app.models.user import User


def _make_engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _demo_service():
    """强制演示模式（清空企业凭据）后构建全新服务，确保测试不依赖 .env。"""
    s = get_settings()
    with patch.object(s, "WECOM_CORP_ID", ""), patch.object(s, "WECOM_SECRET", ""), \
         patch.object(s, "WECOM_AGENT_ID", ""), patch.object(s, "DINGTALK_APP_KEY", ""), \
         patch.object(s, "DINGTALK_APP_SECRET", ""), patch.object(s, "LDAP_URL", ""), \
         patch.object(s, "LDAP_BASE_DN", ""), patch.object(s, "LDAP_BIND_DN", ""), \
         patch.object(s, "LDAP_BIND_PASSWORD", ""):
        from app.services.auth.enterprise_auth_service import EnterpriseAuthService
        return EnterpriseAuthService()


class EnterpriseAuthTests(unittest.TestCase):
    """演示模式下企业登录端到端：创建/复用绑定用户 + 签发 token + 记录日志"""

    def setUp(self):
        self.engine = _make_engine()
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session()

        self.org = Organization(name="试点企业", code="PILOT-01")
        self.db.add(self.org)
        self.db.commit()

        self.service = _demo_service()

    def tearDown(self):
        self.db.close()

    def _count_users(self):
        return self.db.query(User).count()

    def test_wecom_oauth_login_creates_user_and_token(self):
        user, token = self.service.oauth_login(self.db, "wecom", "WECODE-001", "127.0.0.1", "ua")
        self.assertIsNotNone(user)
        self.assertIsNotNone(token)
        self.assertEqual(user.external_provider, "wecom")
        self.assertIsNotNone(user.external_user_id)
        self.assertEqual(user.organization_id, self.org.id)  # 关联 PILOT-01 组织
        self.assertIn("@", user.email)
        self.assertEqual(self._count_users(), 1)

    def test_wecom_oauth_login_same_code_reuses_user(self):
        user1, _ = self.service.oauth_login(self.db, "wecom", "WECODE-001", None, None)
        user2, _ = self.service.oauth_login(self.db, "wecom", "WECODE-001", None, None)
        self.assertEqual(user1.id, user2.id)
        self.assertEqual(self._count_users(), 1)

    def test_dingtalk_oauth_login_creates_user(self):
        user, token = self.service.oauth_login(self.db, "dingtalk", "DING-CODE", None, None)
        self.assertIsNotNone(user)
        self.assertIsNotNone(token)
        self.assertEqual(user.external_provider, "dingtalk")

    def test_ldap_login_creates_user(self):
        user, token = self.service.ldap_login(self.db, "zhangsan", "pw123", None, None)
        self.assertIsNotNone(user)
        self.assertIsNotNone(token)
        self.assertEqual(user.external_provider, "ldap")
        self.assertEqual(user.username, "zhangsan")
        self.assertEqual(self._count_users(), 1)

    def test_ldap_login_same_user_reuses(self):
        user1, _ = self.service.ldap_login(self.db, "zhangsan", "pw123", None, None)
        user2, _ = self.service.ldap_login(self.db, "zhangsan", "pw123", None, None)
        self.assertEqual(user1.id, user2.id)

    def test_ldap_login_empty_credentials_fails(self):
        user, token = self.service.ldap_login(self.db, "", "", None, None)
        self.assertIsNone(user)
        self.assertIsNone(token)

    def test_oauth_login_unknown_provider_returns_none(self):
        user, token = self.service.oauth_login(self.db, "github", "code", None, None)
        self.assertIsNone(user)
        self.assertIsNone(token)

    def test_login_events_recorded(self):
        self.service.oauth_login(self.db, "wecom", "WECODE-002", "10.0.0.1", "ua")
        from app.models.auth_log import LoginLog
        logs = self.db.query(LoginLog).filter(LoginLog.username.isnot(None)).all()
        self.assertTrue(any("wecom" in (l.detail or "") for l in logs))

    def test_oauth_user_password_non_null_and_cannot_local_login(self):
        """MySQL users.hashed_password NOT NULL：OAuth 用户须有随机占位密码，且本地密码登录必然失败"""
        user, _ = self.service.oauth_login(self.db, "wecom", "WECODE-003", None, None)
        self.assertIsNotNone(user.hashed_password)
        other, token = self.service.local_login(self.db, user.username, "any-password", None, None)
        self.assertIsNone(token)


class LoginLockoutPolicyTests(unittest.TestCase):
    """锁定阈值/时长必须来自 LOGIN_MAX_FAIL_COUNT / LOGIN_LOCK_DURATION_MINUTES。

    这两个配置此前被同名的类常量（写死 5 / 30）盖住：运维把 .env 改成 3 次，
    仍要到第 5 次失败才锁。
    """

    def setUp(self):
        from app.core.auth import hash_password

        self.engine = _make_engine()
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session()
        self.user = User(
            username="locktester", email="lock@example.com",
            hashed_password=hash_password("right-password"),
            role="user", status="active",
        )
        self.db.add(self.user)
        self.db.commit()
        self.service = _demo_service()

    def tearDown(self):
        self.db.close()

    def _fail_login(self):
        return self.service.local_login(self.db, "locktester", "wrong-password", "127.0.0.1", "ua")

    def test_lock_threshold_follows_setting(self):
        with patch.object(get_settings(), "LOGIN_MAX_FAIL_COUNT", 2):
            self._fail_login()
            self.db.refresh(self.user)
            self.assertEqual(self.user.status, "active")  # 第 1 次未达阈值
            self._fail_login()
            self.db.refresh(self.user)
            self.assertEqual(self.user.status, "locked")  # 第 2 次即锁定
        self.assertIsNotNone(self.user.locked_until)

    def test_lock_duration_follows_setting(self):
        with patch.object(get_settings(), "LOGIN_MAX_FAIL_COUNT", 1), patch.object(
            get_settings(), "LOGIN_LOCK_DURATION_MINUTES", 90
        ):
            self._fail_login()
        self.db.refresh(self.user)
        locked_until = self.user.locked_until
        if locked_until.tzinfo is not None:  # MySQL 读回带偏移，统一按 naive UTC 比较
            locked_until = locked_until.replace(tzinfo=None)
        minutes = (locked_until - utc_now()).total_seconds() / 60
        self.assertGreater(minutes, 85)
        self.assertLess(minutes, 91)

    def test_locked_account_rejected_then_unlocked_after_expiry(self):
        with patch.object(get_settings(), "LOGIN_MAX_FAIL_COUNT", 1):
            self._fail_login()
        self.db.refresh(self.user)
        # 锁定期内即使密码正确也拒绝
        user, token = self.service.local_login(self.db, "locktester", "right-password", None, None)
        self.assertIsNone(token)
        # 锁定到期后自动解锁并放行
        self.user.locked_until = utc_now() - timedelta(seconds=1)
        self.db.add(self.user)
        self.db.commit()
        user, token = self.service.local_login(self.db, "locktester", "right-password", None, None)
        self.assertIsNotNone(token)
        self.db.refresh(self.user)
        self.assertEqual(self.user.login_fail_count, 0)

    def test_settings_change_takes_effect_without_restart(self):
        """服务是模块级单例：阈值必须每次读配置，不能在构造期快照。"""
        from app.services.auth.enterprise_auth_service import enterprise_auth_service

        with patch.object(get_settings(), "LOGIN_MAX_FAIL_COUNT", 7):
            self.assertEqual(enterprise_auth_service.MAX_LOGIN_FAIL_COUNT, 7)
        with patch.object(get_settings(), "LOGIN_MAX_FAIL_COUNT", 3):
            self.assertEqual(enterprise_auth_service.MAX_LOGIN_FAIL_COUNT, 3)


if __name__ == "__main__":
    unittest.main()
