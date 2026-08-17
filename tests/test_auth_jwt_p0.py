"""P0 JWT / 撤销 / force logout / refresh token 认证测试。

聚焦：
- access JWT 载荷结构（sub/jti/token_version/typ/iat/exp）
- jti 撤销、token_version 失效、用户禁用
- force logout / logout all / logout 撤销
- refresh token 轮换、重放检测、family 撤销、哈希存储
- TokenResponse 向后兼容
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from jose import jwt as jose_jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import create_access_token, hash_password
from app.core.config import get_settings
from app.core.database import Base, get_db
from app.main import app
from app.models.user import User, UserStatus
from app.models.org import Organization, OrganizationMember
from app.models.security_auth import RefreshToken, RevokedToken
from app.services.auth.auth_token_service import auth_token_service

settings = get_settings()


class AuthJwtBase(unittest.TestCase):
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
            username="alice", email="alice@test.com",
            hashed_password=hash_password("pw"), status=UserStatus.active.value,
        )
        self.db.add(self.user)
        self.db.flush()
        self.db.add(OrganizationMember(
            organization_id=self.org.id, user_id=self.user.id, legal_role="admin"))
        self.db.commit()
        self.db.refresh(self.user)
        self.user_id = self.user.id

    def tearDown(self):
        self.db.close()
        app.dependency_overrides.clear()

    def login(self):
        return self.client.post("/api/auth/login", json={"username": "alice", "password": "pw"})

    def _protected(self, token):
        return self.client.get(
            f"/api/legal/orgs/{self.org.id}/cases",
            headers={"Authorization": f"Bearer {token}"},
        )


class JwtPayloadTests(AuthJwtBase):
    def test_access_token_carries_required_claims(self):
        resp = self.login()
        token = resp.json()["data"]["access_token"]
        payload = jose_jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        self.assertEqual(payload["sub"], str(self.user_id))
        self.assertTrue(payload["jti"])
        self.assertEqual(payload["token_version"], 0)
        self.assertEqual(payload["typ"], "access")
        self.assertIn("iat", payload)
        self.assertIn("exp", payload)
        self.assertGreater(payload["exp"], payload["iat"])

    def test_create_access_token_backward_compatible(self):
        """旧式 create_access_token 生成的 token 仍可通过 get_current_user 校验。"""
        token = create_access_token({"sub": self.user_id, "role": self.user.role})
        payload = jose_jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        self.assertEqual(payload["token_version"], 0)
        self.assertTrue(payload["jti"])
        self.assertEqual(self._protected(token).status_code, 200)

    def test_expired_token_rejected(self):
        expired = create_access_token(
            {"sub": self.user_id}, expires_delta=timedelta(seconds=-1)
        )
        self.assertEqual(self._protected(expired).status_code, 401)

    def test_non_access_typ_token_rejected(self):
        """challenge/mfa 类型 token 不能访问业务接口。"""
        payload = auth_token_service.create_access_token(
            self.user, typ="mfa", expires_minutes=10,
        )
        self.assertEqual(self._protected(payload).status_code, 401)


class TokenRevocationJwtTests(AuthJwtBase):
    def test_revoked_jti_rejected_even_if_unexpired(self):
        resp = self.login()
        token = resp.json()["data"]["access_token"]
        payload = jose_jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        self.db.add(RevokedToken(
            jti=payload["jti"], user_id=self.user_id, token_type="access",
            revoke_reason="test",
        ))
        self.db.commit()
        self.assertEqual(self._protected(token).status_code, 401)

    def test_token_version_mismatch_rejected(self):
        resp = self.login()
        token = resp.json()["data"]["access_token"]
        self.user.token_version = 1
        self.db.commit()
        self.assertEqual(self._protected(token).status_code, 401)

    def test_logout_revokes_current_access_token(self):
        resp = self.login()
        token = resp.json()["data"]["access_token"]
        self.assertEqual(self._protected(token).status_code, 200)
        self.client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(self._protected(token).status_code, 401)

    def test_force_logout_via_service_invalidates_tokens(self):
        resp = self.login()
        token = resp.json()["data"]["access_token"]
        from app.services.auth.enterprise_auth_service import enterprise_auth_service

        enterprise_auth_service.force_logout(self.db, self.user_id, operator_id=self.user_id)
        self.assertEqual(self._protected(token).status_code, 401)

    def test_logout_all_invalidates_access_and_refresh(self):
        resp = self.login()
        access = resp.json()["data"]["access_token"]
        refresh = resp.json()["data"]["refresh_token"]
        self.client.post("/api/auth/logout-all", headers={"Authorization": f"Bearer {access}"})
        self.assertEqual(self._protected(access).status_code, 401)
        r = self.client.post("/api/auth/refresh", json={"refresh_token": refresh})
        self.assertEqual(r.status_code, 401)


class RefreshTokenJwtTests(AuthJwtBase):
    def test_refresh_token_stored_only_as_hash(self):
        resp = self.login()
        refresh = resp.json()["data"]["refresh_token"]
        row = self.db.query(RefreshToken).first()
        self.assertIsNotNone(row)
        self.assertNotEqual(row.token_hash, refresh)
        from app.services.auth.auth_token_service import hash_opaque

        self.assertEqual(row.token_hash, hash_opaque(refresh))

    def test_rotation_issues_new_pair(self):
        resp = self.login()
        r1 = self.client.post("/api/auth/refresh", json={
            "refresh_token": resp.json()["data"]["refresh_token"]})
        self.assertEqual(r1.status_code, 200)
        data = r1.json()["data"]
        self.assertIn("access_token", data)
        self.assertIn("refresh_token", data)
        self.assertEqual(self._protected(data["access_token"]).status_code, 200)

    def test_replay_revokes_whole_family(self):
        resp = self.login()
        original = resp.json()["data"]["refresh_token"]

        r1 = self.client.post("/api/auth/refresh", json={"refresh_token": original})
        self.assertEqual(r1.status_code, 200)
        rotated = r1.json()["data"]["refresh_token"]

        # 重放旧 refresh token → 401，且 family 全部失效
        replay = self.client.post("/api/auth/refresh", json={"refresh_token": original})
        self.assertEqual(replay.status_code, 401)

        # 同一 family 新签发的 refresh token 也被撤销
        after = self.client.post("/api/auth/refresh", json={"refresh_token": rotated})
        self.assertEqual(after.status_code, 401)

    def test_refresh_for_disabled_user_rejected(self):
        resp = self.login()
        refresh = resp.json()["data"]["refresh_token"]
        self.user.status = UserStatus.disabled.value
        self.db.commit()
        r = self.client.post("/api/auth/refresh", json={"refresh_token": refresh})
        self.assertEqual(r.status_code, 401)

    def test_logout_revokes_refresh_token(self):
        resp = self.login()
        access = resp.json()["data"]["access_token"]
        refresh = resp.json()["data"]["refresh_token"]
        self.client.post(
            "/api/auth/logout",
            headers={"Authorization": f"Bearer {access}"},
            json={"refresh_token": refresh},
        )
        r = self.client.post("/api/auth/refresh", json={"refresh_token": refresh})
        self.assertEqual(r.status_code, 401)

    def test_token_version_bump_revokes_refresh_tokens(self):
        resp = self.login()
        refresh = resp.json()["data"]["refresh_token"]
        auth_token_service.increment_token_version(self.db, self.user)
        r = self.client.post("/api/auth/refresh", json={"refresh_token": refresh})
        self.assertEqual(r.status_code, 401)


class TokenResponseCompatTests(AuthJwtBase):
    def test_token_response_shape_backward_compatible(self):
        """TokenResponse 保留 access_token + user，新增可选 refresh_token。"""
        resp = self.login()
        data = resp.json()["data"]
        self.assertIn("access_token", data)
        self.assertIn("user", data)
        self.assertEqual(data["user"]["username"], "alice")
        self.assertEqual(data["token_type"], "bearer")
        self.assertIn("refresh_token", data)
        # user 结构不变
        self.assertIn("id", data["user"])
        self.assertIn("email", data["user"])

    def test_register_response_backward_compatible(self):
        resp = self.client.post("/api/auth/register", json={
            "username": "bob", "email": "bob@test.com", "password": "pw123456",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertIn("access_token", data)
        self.assertEqual(data["user"]["username"], "bob")
        # 新注册 token 可访问需认证接口
        me = self.client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {data['access_token']}"}
        )
        self.assertEqual(me.status_code, 200)


if __name__ == "__main__":
    unittest.main()
