"""注册自动创建个人组织（ux-audit P0-1 根治，迁移 0094 / personal_org_service）"""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import hash_password
from app.core.database import Base, get_db
from app.main import app
from app.models.user import User, UserStatus
from app.models.org import Organization, OrganizationMember
from app.services.org.personal_org_service import ensure_personal_org


class PersonalOrgRegistrationTests(unittest.TestCase):
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

    def _register(self, username="newuser", full_name=None):
        payload = {
            "username": username,
            "email": f"{username}@test.com",
            "password": "Str0ngPass!x",
        }
        if full_name is not None:
            payload["full_name"] = full_name
        return self.client.post("/api/auth/register", json=payload)

    def _get_user(self, username):
        return self.db.query(User).filter(User.username == username).first()

    # ──────────────────────────────────────────────
    # 1. 自注册自动创建个人组织
    # ──────────────────────────────────────────────

    def test_register_creates_personal_org(self):
        resp = self._register(username="alice", full_name="爱丽丝")
        self.assertEqual(resp.status_code, 200, resp.text)

        user = self._get_user("alice")
        self.assertIsNotNone(user.organization_id)

        org = self.db.query(Organization).filter(Organization.id == user.organization_id).first()
        self.assertIsNotNone(org)
        self.assertEqual(org.org_type, "personal")
        self.assertEqual(org.code, f"personal-{user.id}")
        self.assertIn("爱丽丝", org.name)

        member = self.db.query(OrganizationMember).filter(
            OrganizationMember.organization_id == org.id,
            OrganizationMember.user_id == user.id,
        ).first()
        self.assertIsNotNone(member)
        self.assertEqual(member.legal_role, "admin")

    def test_register_org_name_falls_back_to_username(self):
        """full_name 为空时组织名回退到 username"""
        resp = self._register(username="bob")
        self.assertEqual(resp.status_code, 200, resp.text)

        user = self._get_user("bob")
        org = self.db.query(Organization).filter(Organization.id == user.organization_id).first()
        self.assertIsNotNone(org)
        self.assertIn("bob", org.name)

    def test_register_duplicate_org_name_recovers(self):
        """同名 full_name 的组织名冲突时加 (user.id) 后缀，注册仍成功"""
        self.db.add(Organization(name="张三的工作台", code="pre-existing"))
        self.db.commit()

        resp = self._register(username="zhangsan", full_name="张三")
        self.assertEqual(resp.status_code, 200, resp.text)

        user = self._get_user("zhangsan")
        org = self.db.query(Organization).filter(Organization.id == user.organization_id).first()
        self.assertIsNotNone(org)
        self.assertEqual(org.name, f"张三({user.id})的工作台")

    # ──────────────────────────────────────────────
    # 2. 邮箱验证码注册同型覆盖
    # ──────────────────────────────────────────────

    def test_register_with_code_creates_personal_org(self):
        with patch(
            "app.api.auth.auth_api.user_auth_service.verify_email_code",
            return_value=True,
        ):
            resp = self.client.post("/api/auth/register-with-code", json={
                "username": "carol",
                "email": "carol@test.com",
                "password": "Str0ngPass!x",
                "code": "123456",
                "full_name": "卡罗尔",
            })
        self.assertEqual(resp.status_code, 200, resp.text)

        user = self._get_user("carol")
        self.assertIsNotNone(user.organization_id)
        org = self.db.query(Organization).filter(Organization.id == user.organization_id).first()
        self.assertEqual(org.org_type, "personal")
        member = self.db.query(OrganizationMember).filter(
            OrganizationMember.organization_id == org.id,
            OrganizationMember.user_id == user.id,
        ).first()
        self.assertEqual(member.legal_role, "admin")

    # ──────────────────────────────────────────────
    # 3. service 幂等性
    # ──────────────────────────────────────────────

    def test_ensure_personal_org_idempotent(self):
        self._register(username="dave")
        user = self._get_user("dave")
        before_count = self.db.query(Organization).count()

        org_again = ensure_personal_org(self.db, user)
        self.db.commit()

        self.assertEqual(self.db.query(Organization).count(), before_count)
        self.assertEqual(org_again.id, user.organization_id)

    # ──────────────────────────────────────────────
    # 4. 注册后组织上下文立即可用
    # ──────────────────────────────────────────────

    def test_register_then_me_has_org(self):
        resp = self._register(username="erin")
        self.assertEqual(resp.status_code, 200, resp.text)
        token = resp.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        me = self.client.get("/api/auth/me", headers=headers)
        self.assertEqual(me.status_code, 200, me.text)

        overview = self.client.get("/api/legal/overview", headers=headers)
        self.assertEqual(overview.status_code, 200, overview.text)
        self.assertIsNotNone(overview.json()["data"]["organization_id"])


if __name__ == "__main__":
    unittest.main()
