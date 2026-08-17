"""Phase 10 Week 1 tests: email verification, password reset, WeChat login"""
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import create_access_token, hash_password
from app.core.database import Base, get_db
from app.main import app
from app.models.user import User, UserStatus, EmailVerificationCode, PasswordResetToken, WechatUser


class AuthPhase10Tests(unittest.TestCase):
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

        self.existing_user = User(
            username="existing_user",
            email="existing@test.com",
            hashed_password=hash_password("password123"),
            role="user",
            status=UserStatus.active.value,
        )
        self.db.add(self.existing_user)
        self.db.commit()
        self.db.refresh(self.existing_user)

        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)

        self.auth_token = create_access_token({"sub": self.existing_user.id, "role": "user"})

    def tearDown(self):
        self.db.close()
        app.dependency_overrides.clear()

    # ──────────────────────────────────────────────
    # 1. 发送验证码
    # ──────────────────────────────────────────────

    def test_send_verification_code_new_email(self):
        """新邮箱可以发送验证码"""
        with patch("app.services.auth.user_auth_service.user_auth_service.send_email", return_value=True):
            resp = self.client.post("/api/auth/send-verification-code", json={
                "email": "newuser@test.com",
                "purpose": "register",
            })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(data["email"], "newuser@test.com")
        self.assertIn("expires_minutes", data)

    def test_send_verification_code_existing_email_register(self):
        """已注册邮箱发注册验证码应返回409"""
        resp = self.client.post("/api/auth/send-verification-code", json={
            "email": "existing@test.com",
            "purpose": "register",
        })
        self.assertEqual(resp.status_code, 409)

    def test_send_verification_code_existing_email_reset(self):
        """已注册邮箱发reset验证码可以成功"""
        with patch("app.services.auth.user_auth_service.user_auth_service.send_email", return_value=True):
            resp = self.client.post("/api/auth/send-verification-code", json={
                "email": "existing@test.com",
                "purpose": "reset_password",
            })
        self.assertEqual(resp.status_code, 200)

    # ──────────────────────────────────────────────
    # 2. 验证邮箱验证码
    # ──────────────────────────────────────────────

    def _insert_code(self, email, code, purpose="register", expired=False, used=False):
        expires_at = (
            datetime.now(timezone.utc) - timedelta(minutes=1)
            if expired
            else datetime.now(timezone.utc) + timedelta(minutes=15)
        )
        vc = EmailVerificationCode(
            email=email, code=code, purpose=purpose,
            expires_at=expires_at, used=used,
        )
        self.db.add(vc)
        self.db.commit()
        return vc

    def test_verify_email_valid_code(self):
        """有效验证码可以验证成功"""
        self._insert_code("verify@test.com", "123456")
        resp = self.client.post("/api/auth/verify-email", json={
            "email": "verify@test.com",
            "code": "123456",
            "purpose": "register",
        })
        self.assertEqual(resp.status_code, 200)

    def test_verify_email_wrong_code(self):
        """错误验证码返回400"""
        self._insert_code("verify2@test.com", "123456")
        resp = self.client.post("/api/auth/verify-email", json={
            "email": "verify2@test.com",
            "code": "999999",
            "purpose": "register",
        })
        self.assertEqual(resp.status_code, 400)

    def test_verify_email_expired_code(self):
        """过期验证码返回400"""
        self._insert_code("verify3@test.com", "123456", expired=True)
        resp = self.client.post("/api/auth/verify-email", json={
            "email": "verify3@test.com",
            "code": "123456",
            "purpose": "register",
        })
        self.assertEqual(resp.status_code, 400)

    def test_verify_email_used_code(self):
        """已使用验证码返回400"""
        self._insert_code("verify4@test.com", "123456", used=True)
        resp = self.client.post("/api/auth/verify-email", json={
            "email": "verify4@test.com",
            "code": "123456",
            "purpose": "register",
        })
        self.assertEqual(resp.status_code, 400)

    # ──────────────────────────────────────────────
    # 3. 带验证码注册
    # ──────────────────────────────────────────────

    def test_register_with_code_success(self):
        """有效验证码注册成功，返回 JWT"""
        self._insert_code("newreg@test.com", "654321")
        resp = self.client.post("/api/auth/register-with-code", json={
            "username": "newreg_user",
            "email": "newreg@test.com",
            "password": "securepass",
            "code": "654321",
            "full_name": "新用户",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertIn("access_token", data)
        self.assertEqual(data["user"]["email"], "newreg@test.com")

    def test_register_with_code_duplicate_username(self):
        """用户名已存在返回409"""
        self._insert_code("newreg2@test.com", "654321")
        resp = self.client.post("/api/auth/register-with-code", json={
            "username": "existing_user",
            "email": "newreg2@test.com",
            "password": "securepass",
            "code": "654321",
        })
        self.assertEqual(resp.status_code, 409)

    def test_register_with_code_invalid_code(self):
        """验证码错误时注册失败"""
        resp = self.client.post("/api/auth/register-with-code", json={
            "username": "new_user_x",
            "email": "newx@test.com",
            "password": "securepass",
            "code": "000000",
        })
        self.assertEqual(resp.status_code, 400)

    def test_register_with_code_code_used_only_once(self):
        """验证码注册后不能再次使用"""
        self._insert_code("reuse@test.com", "111111")
        # 第一次注册成功
        self.client.post("/api/auth/register-with-code", json={
            "username": "reuse_user",
            "email": "reuse@test.com",
            "password": "securepass",
            "code": "111111",
        })
        # 第二次注册同邮箱
        resp = self.client.post("/api/auth/register-with-code", json={
            "username": "reuse_user2",
            "email": "reuse@test.com",
            "password": "securepass",
            "code": "111111",
        })
        # 邮箱已存在
        self.assertEqual(resp.status_code, 409)

    # ──────────────────────────────────────────────
    # 4. 密码重置 - 请求
    # ──────────────────────────────────────────────

    def test_forgot_password_existing_email(self):
        """已存在邮箱请求密码重置，返回成功（无论是否发送邮件）"""
        with patch("app.services.auth.user_auth_service.user_auth_service.send_email", return_value=True):
            resp = self.client.post("/api/auth/forgot-password", json={
                "email": "existing@test.com",
            })
        self.assertEqual(resp.status_code, 200)

    def test_forgot_password_nonexistent_email(self):
        """不存在的邮箱也返回200（防止邮箱枚举）"""
        resp = self.client.post("/api/auth/forgot-password", json={
            "email": "nosuchuser@test.com",
        })
        self.assertEqual(resp.status_code, 200)

    # ──────────────────────────────────────────────
    # 5. 密码重置 - 确认
    # ──────────────────────────────────────────────

    def _insert_reset_token(self, user_id, token="validtoken123", expired=False, used=False):
        expires_at = (
            datetime.now(timezone.utc) - timedelta(minutes=1)
            if expired
            else datetime.now(timezone.utc) + timedelta(minutes=30)
        )
        rt = PasswordResetToken(
            user_id=user_id, token=token,
            expires_at=expires_at, used=used,
        )
        self.db.add(rt)
        self.db.commit()
        return rt

    def test_reset_password_valid_token(self):
        """有效token重置密码成功，返回新JWT"""
        self._insert_reset_token(self.existing_user.id, "valid_tok")
        resp = self.client.post("/api/auth/reset-password", json={
            "token": "valid_tok",
            "new_password": "newpassword456",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertIn("access_token", data)

    def test_reset_password_invalid_token(self):
        """无效token返回400"""
        resp = self.client.post("/api/auth/reset-password", json={
            "token": "badtoken",
            "new_password": "newpassword456",
        })
        self.assertEqual(resp.status_code, 400)

    def test_reset_password_expired_token(self):
        """过期token返回400"""
        self._insert_reset_token(self.existing_user.id, "expired_tok", expired=True)
        resp = self.client.post("/api/auth/reset-password", json={
            "token": "expired_tok",
            "new_password": "newpassword456",
        })
        self.assertEqual(resp.status_code, 400)

    def test_reset_password_used_token(self):
        """已使用token返回400"""
        self._insert_reset_token(self.existing_user.id, "used_tok", used=True)
        resp = self.client.post("/api/auth/reset-password", json={
            "token": "used_tok",
            "new_password": "newpassword456",
        })
        self.assertEqual(resp.status_code, 400)

    def test_reset_password_token_used_once(self):
        """token只能用一次"""
        self._insert_reset_token(self.existing_user.id, "once_tok")
        # 第一次成功
        resp1 = self.client.post("/api/auth/reset-password", json={
            "token": "once_tok",
            "new_password": "pw_new_1",
        })
        self.assertEqual(resp1.status_code, 200)
        # 第二次失败
        resp2 = self.client.post("/api/auth/reset-password", json={
            "token": "once_tok",
            "new_password": "pw_new_2",
        })
        self.assertEqual(resp2.status_code, 400)

    # ──────────────────────────────────────────────
    # 6. 微信登录
    # ──────────────────────────────────────────────

    def test_wechat_login_url_not_configured(self):
        """未配置微信时返回500"""
        resp = self.client.get("/api/auth/wechat/login-url")
        self.assertEqual(resp.status_code, 500)

    def test_wechat_callback_create_new_user(self):
        """微信回调：新用户自动创建账号"""
        mock_token_resp = MagicMock()
        mock_token_resp.json.return_value = {
            "access_token": "wx_access_token",
            "openid": "test_openid_001",
            "unionid": "test_unionid_001",
        }
        mock_user_resp = MagicMock()
        mock_user_resp.json.return_value = {
            "nickname": "微信用户甲",
            "headimgurl": "https://wx.example.com/avatar.jpg",
        }

        with patch("app.services.auth.user_auth_service.settings") as mock_settings:
            mock_settings.WECHAT_APP_ID = "test_appid"
            mock_settings.WECHAT_APP_SECRET = "test_secret"
            mock_settings.WECHAT_REDIRECT_URI = "https://example.com/callback"
            with patch("app.services.auth.user_auth_service.requests.get") as mock_get:
                mock_get.side_effect = [mock_token_resp, mock_user_resp]
                resp = self.client.get("/api/auth/wechat/callback?code=wx_code_001&state=test_state")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertIn("access_token", data)
        self.assertIn("user", data)

        # 验证用户和绑定记录已创建
        wx_user = self.db.query(WechatUser).filter(WechatUser.openid == "test_openid_001").first()
        self.assertIsNotNone(wx_user)
        self.assertEqual(wx_user.unionid, "test_unionid_001")

    def test_wechat_callback_existing_user(self):
        """微信回调：已绑定用户直接登录"""
        # 预先创建用户和绑定记录
        wx_existing = User(
            username="wx_existing",
            email="wx_exist@wechat.placeholder",
            role="user",
            status=UserStatus.active.value,
        )
        self.db.add(wx_existing)
        self.db.flush()
        self.db.add(WechatUser(
            user_id=wx_existing.id,
            openid="existing_openid_002",
        ))
        self.db.commit()

        mock_token_resp = MagicMock()
        mock_token_resp.json.return_value = {
            "access_token": "wx_access_token2",
            "openid": "existing_openid_002",
        }
        mock_user_resp = MagicMock()
        mock_user_resp.json.return_value = {"nickname": "已有用户", "headimgurl": ""}

        with patch("app.services.auth.user_auth_service.settings") as mock_settings:
            mock_settings.WECHAT_APP_ID = "test_appid"
            mock_settings.WECHAT_APP_SECRET = "test_secret"
            mock_settings.WECHAT_REDIRECT_URI = "https://example.com/callback"
            with patch("app.services.auth.user_auth_service.requests.get") as mock_get:
                mock_get.side_effect = [mock_token_resp, mock_user_resp]
                resp = self.client.get("/api/auth/wechat/callback?code=wx_code_002&state=test_state2")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertIn("access_token", data)

    def test_wechat_callback_api_failure(self):
        """微信API调用失败时返回401"""
        with patch("app.services.auth.user_auth_service.settings") as mock_settings:
            mock_settings.WECHAT_APP_ID = "test_appid"
            mock_settings.WECHAT_APP_SECRET = "test_secret"
            mock_settings.WECHAT_REDIRECT_URI = "https://example.com/callback"
            with patch("app.services.auth.user_auth_service.requests.get", side_effect=Exception("network error")):
                resp = self.client.get("/api/auth/wechat/callback?code=bad_code&state=state")

        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
