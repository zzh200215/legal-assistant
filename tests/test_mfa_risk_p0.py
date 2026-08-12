"""P0 TOTP MFA / 设备 IP 风险 / MFA challenge 确定性测试。

重点验证：
- challenge token 绝不能通过业务接口（访问 / refresh 均拒绝）。
- challenge 单次使用、过期失效。
- 风险识别确定性：新设备=中危、IP 变化=中危、已知设备=低危、连续失败=高危。
- 风险驱动登录策略：高危设备拒绝登录；新设备登录触发 MFA（对已启用用户）。
- MFA 已启用时 setup 拒绝重置。
"""

import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import create_access_token, hash_password
from app.core.database import Base, get_db
from app.main import app
from app.models.user import User, UserStatus
from app.models.org import Organization, OrganizationMember
from app.models.security_auth import AuthDevice, MFAChallenge, MFARecoveryCode
from app.services.auth_token_service import auth_token_service
from app.services.mfa_service import mfa_service


class MfaRiskBase(unittest.TestCase):
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

        self.org = Organization(name="律所A", code="ORG_A")
        self.db.add(self.org)
        self.db.flush()
        self.user = User(
            username="mfa_user", email="mfa@test.com",
            hashed_password=hash_password("pw"), status=UserStatus.active.value,
        )
        self.db.add(self.user)
        self.db.flush()
        self.db.add(OrganizationMember(
            organization_id=self.org.id, user_id=self.user.id, legal_role="admin"))
        self.db.commit()
        self.db.refresh(self.user)

    def tearDown(self):
        self.db.close()
        app.dependency_overrides.clear()

    def _auth(self):
        return {"Authorization": f"Bearer {create_access_token({'sub': self.user.id})}"}

    def _setup_mfa(self):
        resp = self.client.post("/api/auth/mfa/setup", headers=self._auth())
        self.assertEqual(resp.status_code, 200)
        otpauth = resp.json()["data"]["otpauth_uri"]
        secret = otpauth.split("secret=")[1].split("&")[0]
        code = mfa_service.totp_code(secret)
        confirm = self.client.post(f"/api/auth/mfa/confirm?code={code}", headers=self._auth())
        self.assertEqual(confirm.status_code, 200)
        recovery_codes = confirm.json()["data"]["recovery_codes"]
        return secret, recovery_codes

    def _login(self, device_id=None):
        headers = {"X-Device-Id": device_id} if device_id else None
        return self.client.post(
            "/api/auth/login", json={"username": "mfa_user", "password": "pw"},
            headers=headers,
        )

    def _protected(self, token):
        return self.client.get(
            f"/api/legal/orgs/{self.org.id}/cases",
            headers={"Authorization": f"Bearer {token}"},
        )


class ChallengeIsolationTests(MfaRiskBase):
    """challenge token 绝不能通过业务接口。"""

    def test_challenge_not_accepted_on_business_api(self):
        self._setup_mfa()
        login = self._login()
        challenge = login.json()["data"]["challenge"]
        resp = self._protected(challenge)
        self.assertEqual(resp.status_code, 401)

    def test_challenge_not_accepted_on_refresh_endpoint(self):
        self._setup_mfa()
        login = self._login()
        challenge = login.json()["data"]["challenge"]
        resp = self.client.post("/api/auth/refresh", json={"refresh_token": challenge})
        self.assertEqual(resp.status_code, 401)

    def test_challenge_is_not_a_jwt(self):
        """challenge 是随机不透明串，不含 JWT 结构。"""
        self._setup_mfa()
        login = self._login()
        challenge = login.json()["data"]["challenge"]
        self.assertNotIn(".", challenge)
        from jose import jwt as jose_jwt

        self.assertRaises(Exception, jose_jwt.decode, challenge, "not-a-key", algorithms=["HS256"])

    def test_challenge_single_use(self):
        self._setup_mfa()
        login = self._login()
        challenge = login.json()["data"]["challenge"]
        code = self._current_totp()
        v1 = self.client.post(f"/api/auth/mfa/verify?challenge={challenge}&code={code}")
        self.assertEqual(v1.status_code, 200)
        # 同一 challenge 再次使用 → 401
        code2 = self._current_totp()
        v2 = self.client.post(f"/api/auth/mfa/verify?challenge={challenge}&code={code2}")
        self.assertEqual(v2.status_code, 401)

    def test_challenge_expired(self):
        self._setup_mfa()
        login = self._login()
        challenge = login.json()["data"]["challenge"]
        row = self.db.query(MFAChallenge).filter(
            MFAChallenge.challenge_jti == challenge).first()
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        self.db.commit()
        code = self._current_totp()
        resp = self.client.post(f"/api/auth/mfa/verify?challenge={challenge}&code={code}")
        self.assertEqual(resp.status_code, 401)

    def _current_totp(self):
        cred = mfa_service.get_credential(self.db, self.user.id)
        from app.core.encryption import decrypt_text

        secret = decrypt_text(cred.secret_encrypted)
        return mfa_service.totp_code(secret)


class RiskDrivenLoginTests(MfaRiskBase):
    """风险识别确定性驱动登录流程。"""

    def test_new_device_login_triggers_mfa_with_medium_risk(self):
        self._setup_mfa()
        login = self._login(device_id="brand-new-device")
        data = login.json()["data"]
        self.assertTrue(data["mfa_required"])
        self.assertEqual(data["risk_level"], "medium")
        self.assertIn("new_device", data["risk_reasons"])
        # 新设备必须通过 MFA 第二步
        code = self._current_totp()
        ch = data["challenge"]
        verify = self.client.post(f"/api/auth/mfa/verify?challenge={ch}&code={code}")
        self.assertEqual(verify.status_code, 200)

    def test_mfa_user_always_challenged_even_known_device(self):
        """已启用 MFA 的用户即使已知低危设备也必须走 MFA（不能直接拿到 access token）。"""
        self._setup_mfa()
        self._login(device_id="known-dev")
        # 完成一次 MFA 验证，使设备成为已知
        self._complete_mfa()
        # 再次登录同一设备：风险降为 low，但仍要求 MFA
        login = self._login(device_id="known-dev")
        data = login.json()["data"]
        self.assertTrue(data["mfa_required"])
        self.assertEqual(data["risk_level"], "low")
        self.assertNotIn("access_token", data)

    def test_high_risk_device_blocks_non_mfa_login(self):
        """未启用 MFA 的用户在高危设备上登录被拒绝。"""
        # 预置高危设备
        self.db.add(AuthDevice(
            user_id=self.user.id, device_id="compromised-dev",
            risk_level="high", risk_reason="reported_compromise",
        ))
        self.db.commit()
        resp = self._login(device_id="compromised-dev")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"]["code"], "LOGIN_RISK_BLOCKED")

    def test_risk_evolves_from_medium_to_low_for_known_device(self):
        """同一设备第二次登录时，风险由中危降为低危（确定性记录生效）。"""
        self._setup_mfa()
        # 第一次：新设备 → medium
        r1 = self._login(device_id="stable-dev")
        self.assertEqual(r1.json()["data"]["risk_level"], "medium")
        # 完成同设备 MFA 验证，使设备成为已知
        code = self._current_totp()
        ch = r1.json()["data"]["challenge"]
        self.assertEqual(
            self.client.post(f"/api/auth/mfa/verify?challenge={ch}&code={code}").status_code,
            200,
        )
        # 第二次：已知设备 → low
        r2 = self._login(device_id="stable-dev")
        self.assertEqual(r2.json()["data"]["risk_level"], "low")

    def _complete_mfa(self):
        code = self._current_totp()
        login = self._login(device_id="known-dev")
        ch = login.json()["data"]["challenge"]
        return self.client.post(f"/api/auth/mfa/verify?challenge={ch}&code={code}")

    def _current_totp(self):
        cred = mfa_service.get_credential(self.db, self.user.id)
        from app.core.encryption import decrypt_text

        secret = decrypt_text(cred.secret_encrypted)
        return mfa_service.totp_code(secret)


class RiskDeterminismTests(MfaRiskBase):
    """风险评估函数确定性（不依赖外部服务）。"""

    def test_new_device_medium(self):
        risk = auth_token_service.assess_risk(
            self.db, self.user, device_id="d1", ip_address="10.0.0.1", user_agent="ua")
        self.assertEqual(risk.risk_level, "medium")
        self.assertTrue(risk.requires_mfa)
        self.assertIn("new_device", risk.reasons)

    def test_ip_change_medium(self):
        auth_token_service.record_device(
            self.db, self.user, device_id="d2", ip_address="10.0.0.1",
            user_agent="ua", risk=auth_token_service.assess_risk(
                self.db, self.user, device_id="d2", ip_address="10.0.0.1", user_agent="ua"))
        risk = auth_token_service.assess_risk(
            self.db, self.user, device_id="d2", ip_address="10.0.0.99", user_agent="ua")
        self.assertEqual(risk.risk_level, "medium")
        self.assertIn("ip_changed", risk.reasons)

    def test_known_device_low(self):
        auth_token_service.record_device(
            self.db, self.user, device_id="d3", ip_address="10.0.0.2",
            user_agent="ua", risk=auth_token_service.assess_risk(
                self.db, self.user, device_id="d3", ip_address="10.0.0.2", user_agent="ua"))
        risk = auth_token_service.assess_risk(
            self.db, self.user, device_id="d3", ip_address="10.0.0.2", user_agent="ua")
        self.assertEqual(risk.risk_level, "low")
        self.assertFalse(risk.requires_mfa)

    def test_repeated_failures_high(self):
        risk = auth_token_service.assess_risk(
            self.db, self.user, device_id="d4", ip_address="10.0.0.3",
            user_agent="ua", login_failures=3)
        self.assertEqual(risk.risk_level, "high")


class MfaSetupGuardTests(MfaRiskBase):
    def test_setup_refuses_when_already_enabled(self):
        self._setup_mfa()
        resp = self.client.post("/api/auth/mfa/setup", headers=self._auth())
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"]["code"], "MFA_ALREADY_ENABLED")

    def test_confirm_with_wrong_code_fails(self):
        resp = self.client.post("/api/auth/mfa/setup", headers=self._auth())
        otpauth = resp.json()["data"]["otpauth_uri"]
        self.assertIn("secret=", otpauth)
        confirm = self.client.post(
            "/api/auth/mfa/confirm?code=000000", headers=self._auth())
        self.assertEqual(confirm.status_code, 400)

    def test_disable_requires_valid_code(self):
        self._setup_mfa()
        bad = self.client.post("/api/auth/mfa/disable?code=000000", headers=self._auth())
        self.assertEqual(bad.status_code, 400)
        # 仍处于启用状态
        self.assertTrue(mfa_service.mfa_enabled(self.db, self.user.id))

    def test_recovery_codes_hashed_in_db(self):
        self._setup_mfa()
        from app.services.auth_token_service import hash_opaque

        row = self.db.query(MFARecoveryCode).first()
        self.assertIsNotNone(row)
        # 恢复码只存哈希，不存明文
        self.assertNotEqual(row.code_hash, "AAA")
        self.assertEqual(len(row.code_hash), 64)


if __name__ == "__main__":
    unittest.main()
