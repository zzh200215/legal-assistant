"""P0 认证、RBAC 与组织权限测试。

覆盖：
- AuthorizationService 角色矩阵与统一 can/require/scope_query
- 文档列表/详情规则一致性与跨部门隔离
- JWT jti 撤销、token_version 失效、force logout / logout all
- refresh token 轮换与重放检测
- MFA（TOTP/恢复码/challenge/风险）
- 长流程权限快照（普通角色变化保持、硬撤销终止）
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import create_access_token, hash_password
from app.core.database import Base, get_db
from app.main import app
from app.models.user import User, UserStatus
from app.models.org import Organization, OrganizationMember, Department, LegalMemberRole
from app.models.legal import LegalCase
from app.models.document import Document, DocumentAccessRule
from app.models.legal_portal import LegalCaseMember, LegalPortalLink
from app.models.security_auth import MFAChallenge
from app.services.org.authorization_service import (
    AuthorizationContext,
    PermissionAction,
    authorization_service,
)
from app.services.auth.auth_token_service import auth_token_service
from app.services.auth.mfa_service import mfa_service


def _now():
    return datetime.now(timezone.utc)


class AuthSecurityBase(unittest.TestCase):
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
        self._seed()

    def tearDown(self):
        self.db.close()
        app.dependency_overrides.clear()

    def _seed(self):
        self.org_a = Organization(name="律所A", code="ORG_A")
        self.org_b = Organization(name="律所B", code="ORG_B")
        self.db.add_all([self.org_a, self.org_b])
        self.db.flush()

        self.admin = self._user("admin_a")
        self.reviewer = self._user("reviewer_a")
        self.editor = self._user("editor_a")
        self.client_u = self._user("client_a")
        self.b_admin = self._user("admin_b")

        self.m_admin = self._member(self.org_a, self.admin, "admin")
        self.m_reviewer = self._member(self.org_a, self.reviewer, "reviewer")
        self.m_editor = self._member(self.org_a, self.editor, "editor")
        self.m_client = self._member(self.org_a, self.client_u, "client")
        self.m_b_admin = self._member(self.org_b, self.b_admin, "admin")

        self.case_a = LegalCase(
            organization_id=self.org_a.id, user_id=self.admin.id,
            title="案件A", case_type="labor_dispute", status="in_progress",
        )
        self.db.add(self.case_a)
        self.db.commit()
        self.db.refresh(self.case_a)
        # 默认普通成员关系：admin 是 owner
        self.db.add(LegalCaseMember(
            case_id=self.case_a.id, organization_id=self.org_a.id,
            user_id=self.admin.id, case_role="owner", granted_by=self.admin.id,
        ))
        self.db.commit()

    def _user(self, username):
        u = User(username=username, email=f"{username}@test.com",
                 hashed_password=hash_password("pw"), status=UserStatus.active.value)
        self.db.add(u)
        self.db.flush()
        return u

    def _member(self, org, user, role):
        m = OrganizationMember(organization_id=org.id, user_id=user.id, legal_role=role)
        self.db.add(m)
        self.db.flush()
        return m

    def _doc(self, owner, *, scope="private", org=None, dept=None, **kw):
        d = Document(
            user_id=owner.id, title=kw.get("title", "doc"), file_path="/tmp/x.pdf",
            file_type="pdf", permission_scope=scope, organization_id=org.id if org else None,
            department_id=dept.id if dept else None, sensitivity_level="internal",
            download_enabled=True, watermark_required=False, status="parsed",
        )
        self.db.add(d)
        self.db.flush()
        return d

    def _ctx(self, user, org=None):
        return authorization_service.build_context(self.db, user, org_id=org.id if org else None)

    def login(self, username):
        return self.client.post("/api/auth/login", json={"username": username, "password": "pw"})


# ──────────────────────────────────────────────────────────────────────────────
# 1. 角色矩阵
# ──────────────────────────────────────────────────────────────────────────────

class RoleMatrixTests(AuthSecurityBase):
    def test_min_role_levels(self):
        cases = [
            (self.admin, PermissionAction.CASE_CREATE, True),
            (self.reviewer, PermissionAction.CASE_CREATE, True),
            (self.editor, PermissionAction.CASE_CREATE, True),
            (self.client_u, PermissionAction.CASE_CREATE, False),
            (self.client_u, PermissionAction.ORG_READ, True),
            (self.admin, PermissionAction.ORG_MANAGE_MEMBERS, True),
            (self.reviewer, PermissionAction.ORG_MANAGE_MEMBERS, False),
            (self.editor, PermissionAction.CASE_PUBLISH, False),
            (self.reviewer, PermissionAction.CASE_PUBLISH, True),
            (self.editor, PermissionAction.DOCUMENT_CREATE, True),
            (self.client_u, PermissionAction.DOCUMENT_CREATE, False),
            (self.reviewer, PermissionAction.DOCUMENT_REVIEW, True),
            (self.editor, PermissionAction.DOCUMENT_REVIEW, False),
            (self.admin, PermissionAction.ORG_MANAGE_BILLING, True),
            (self.editor, PermissionAction.ORG_MANAGE_BILLING, False),
        ]
        for user, action, expected in cases:
            with self.subTest(user=user.username, action=action):
                ctx = self._ctx(user, self.org_a)
                self.assertEqual(
                    authorization_service.can(self.db, ctx, action), expected,
                )

    def test_non_member_cannot_perform_any_action(self):
        # 组织 B 的管理员，不是组织 A 成员
        ctx = self._ctx(self.b_admin, self.org_a)
        self.assertFalse(ctx.is_org_member)
        self.assertFalse(authorization_service.can(self.db, ctx, PermissionAction.ORG_READ))

    def test_require_raises_403_for_non_member(self):
        ctx = self._ctx(self.b_admin, self.org_a)
        with self.assertRaises(Exception) as raised:
            authorization_service.require(self.db, ctx, PermissionAction.ORG_READ)
        self.assertEqual(raised.exception.status_code, 403)

    def test_system_admin_does_not_bypass_org_boundary(self):
        """系统 admin（User.role=admin）不是组织成员时不得读取租户资源。"""
        sys_admin = self._user("sys_admin")
        sys_admin.role = "admin"
        self.db.commit()
        ctx = self._ctx(sys_admin, self.org_a)
        self.assertFalse(authorization_service.can(self.db, ctx, PermissionAction.CASE_READ))


# ──────────────────────────────────────────────────────────────────────────────
# 2. 文档权限
# ──────────────────────────────────────────────────────────────────────────────

class DocumentScopeTests(AuthSecurityBase):
    def test_owner_accesses_private_doc(self):
        doc = self._doc(self.editor, scope="private")
        ctx = self._ctx(self.editor, self.org_a)
        self.assertTrue(authorization_service.can_access_document(self.db, ctx, doc))
        other = self._ctx(self.reviewer, self.org_a)
        self.assertFalse(authorization_service.can_access_document(self.db, other, doc))

    def test_organization_scope_same_org(self):
        doc = self._doc(self.admin, scope="organization", org=self.org_a)
        editor_ctx = self._ctx(self.editor, self.org_a)
        self.assertTrue(authorization_service.can_access_document(self.db, editor_ctx, doc))
        b_ctx = self._ctx(self.b_admin, self.org_b)
        self.assertFalse(authorization_service.can_access_document(self.db, b_ctx, doc))

    def test_department_scope_same_department(self):
        dept1 = Department(organization_id=self.org_a.id, name="法务一部", code="D1")
        dept2 = Department(organization_id=self.org_a.id, name="法务二部", code="D2")
        self.db.add_all([dept1, dept2])
        self.db.commit()
        doc = self._doc(self.admin, scope="department", org=self.org_a, dept=dept1)
        self.admin.department_id = dept1.id
        self.editor.department_id = dept1.id
        self.reviewer.department_id = dept2.id
        self.db.commit()

        same_dept = self._ctx(self.editor, self.org_a)
        self.assertTrue(authorization_service.can_access_document(self.db, same_dept, doc))
        other_dept = self._ctx(self.reviewer, self.org_a)
        self.assertFalse(authorization_service.can_access_document(self.db, other_dept, doc))

    def test_user_and_role_share_rules(self):
        doc = self._doc(self.admin, scope="restricted")
        self.db.add(DocumentAccessRule(
            document_id=doc.id, subject_type="user", subject_value=str(self.editor.id),
            permission="read",
        ))
        self.db.add(DocumentAccessRule(
            document_id=doc.id, subject_type="role", subject_value="reviewer",
            permission="read",
        ))
        self.db.commit()
        editor_ctx = self._ctx(self.editor, self.org_a)
        self.assertTrue(authorization_service.can_access_document(self.db, editor_ctx, doc))
        reviewer_ctx = self._ctx(self.reviewer, self.org_a)
        self.assertTrue(authorization_service.can_access_document(self.db, reviewer_ctx, doc))
        client_ctx = self._ctx(self.client_u, self.org_a)
        self.assertFalse(authorization_service.can_access_document(self.db, client_ctx, doc))

    def test_write_rule_required_for_modify(self):
        doc = self._doc(self.admin, scope="restricted")
        self.db.add(DocumentAccessRule(
            document_id=doc.id, subject_type="user", subject_value=str(self.editor.id),
            permission="read",
        ))
        self.db.commit()
        editor_ctx = self._ctx(self.editor, self.org_a)
        self.assertTrue(authorization_service.can_access_document(self.db, editor_ctx, doc, write=False))
        self.assertFalse(authorization_service.can_access_document(self.db, editor_ctx, doc, write=True))

    def test_public_doc_default_limited_to_org(self):
        doc = self._doc(self.admin, scope="public", org=self.org_a)
        editor_ctx = self._ctx(self.editor, self.org_a)
        self.assertTrue(authorization_service.can_access_document(self.db, editor_ctx, doc))
        # 禁止默认跨租户公开
        b_ctx = self._ctx(self.b_admin, self.org_b)
        self.assertFalse(authorization_service.can_access_document(self.db, b_ctx, doc))

    def test_list_and_detail_consistency(self):
        """列表（SQL scope filter）结果中的每一项都可通过详情规则访问，且不反向泄漏。"""
        dept1 = Department(organization_id=self.org_a.id, name="法务一部", code="D1")
        self.db.add(dept1)
        self.db.commit()
        docs = [
            self._doc(self.admin, scope="private"),
            self._doc(self.admin, scope="organization", org=self.org_a),
            self._doc(self.admin, scope="public", org=self.org_a),
            self._doc(self.admin, scope="department", org=self.org_a, dept=dept1),
        ]
        self.editor.department_id = dept1.id
        self.db.commit()
        self.admin.department_id = dept1.id
        self.db.commit()

        ctx = self._ctx(self.editor, self.org_a)
        listed_ids = {
            row.id
            for row in authorization_service.scope_query(
                self.db, Document, ctx, PermissionAction.DOCUMENT_READ
            ).all()
        }
        for doc in docs:
            with self.subTest(doc_id=doc.id):
                visible_in_list = doc.id in listed_ids
                visible_in_detail = authorization_service.can_access_document(self.db, ctx, doc)
                self.assertEqual(visible_in_list, visible_in_detail)

        # 跨组织用户列表与详情一致为空
        b_ctx = self._ctx(self.b_admin, self.org_b)
        b_listed = authorization_service.scope_query(
            self.db, Document, b_ctx, PermissionAction.DOCUMENT_READ
        ).all()
        for doc in docs:
            self.assertNotIn(doc.id, [d.id for d in b_listed])
            self.assertFalse(authorization_service.can_access_document(self.db, b_ctx, doc))

    def test_client_cannot_read_internal_case(self):
        """client 只能读自己创建或活跃成员的案件；普通案件对其隐藏（403）。"""
        # editor 无权访问，client 创建的案件（client 不是 owner 且非成员）→ 403
        case = LegalCase(
            organization_id=self.org_a.id, user_id=self.admin.id,
            title="内部案件", case_type="other",
        )
        self.db.add(case)
        self.db.commit()
        client_ctx = self._ctx(self.client_u, self.org_a)
        self.assertFalse(authorization_service.can_read_case(self.db, client_ctx, case))
        editor_ctx = self._ctx(self.editor, self.org_a)
        self.assertTrue(authorization_service.can_read_case(self.db, editor_ctx, case))


# ──────────────────────────────────────────────────────────────────────────────
# 3. 严格案件
# ──────────────────────────────────────────────────────────────────────────────

class StrictCaseTests(AuthSecurityBase):
    def test_strict_case_requires_active_member(self):
        self.case_a.is_strict_mode = 1
        self.db.add(LegalCaseMember(
            case_id=self.case_a.id, organization_id=self.org_a.id,
            user_id=self.editor.id, case_role="collaborator", granted_by=self.admin.id,
        ))
        self.db.commit()

        editor_ctx = self._ctx(self.editor, self.org_a)
        self.assertTrue(authorization_service.can_read_case(self.db, editor_ctx, self.case_a))
        reviewer_ctx = self._ctx(self.reviewer, self.org_a)
        self.assertFalse(authorization_service.can_read_case(self.db, reviewer_ctx, self.case_a))

    def test_strict_case_revoked_member_fails(self):
        self.case_a.is_strict_mode = 1
        self.db.add(LegalCaseMember(
            case_id=self.case_a.id, organization_id=self.org_a.id,
            user_id=self.editor.id, case_role="collaborator", granted_by=self.admin.id,
            revoked_at=_now(),
        ))
        self.db.commit()
        editor_ctx = self._ctx(self.editor, self.org_a)
        self.assertFalse(authorization_service.can_read_case(self.db, editor_ctx, self.case_a))


# ──────────────────────────────────────────────────────────────────────────────
# 4. JWT 撤销 / token_version / force logout
# ──────────────────────────────────────────────────────────────────────────────

class TokenRevocationTests(AuthSecurityBase):
    def _protected(self, token):
        return self.client.get(
            f"/api/legal/orgs/{self.org_a.id}/cases",
            headers={"Authorization": f"Bearer {token}"},
        )

    def test_revoked_access_token_immediately_invalid(self):
        resp = self.login("admin_a")
        access = resp.json()["data"]["access_token"]
        self.assertEqual(self._protected(access).status_code, 200)

        # 登出撤销当前 access token
        logout = self.client.post(
            "/api/auth/logout", headers={"Authorization": f"Bearer {access}"}
        )
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(self._protected(access).status_code, 401)

    def test_token_version_increment_invalidates_old_token(self):
        resp = self.login("admin_a")
        access = resp.json()["data"]["access_token"]
        self.assertEqual(self._protected(access).status_code, 200)

        auth_token_service.increment_token_version(self.db, self.admin)
        self.assertEqual(self._protected(access).status_code, 401)

    def test_force_logout_invalidates_tokens(self):
        resp = self.login("editor_a")
        access = resp.json()["data"]["access_token"]
        self.assertEqual(self._protected(access).status_code, 200)

        # 系统 admin 才能 force-logout
        sys_admin = User(
            username="sys_admin", email="sys@test.com",
            hashed_password=hash_password("pw"), role="admin", status=UserStatus.active.value,
        )
        self.db.add(sys_admin)
        self.db.commit()
        admin_h = {"Authorization": f"Bearer {create_access_token({'sub': sys_admin.id})}"}
        force = self.client.post(
            f"/api/auth/users/{self.editor.id}/force-logout", headers=admin_h
        )
        self.assertEqual(force.status_code, 200)
        # 旧 token 立即失效（而非仅写审计日志）
        self.assertEqual(self._protected(access).status_code, 401)

    def test_logout_all_devices(self):
        resp = self.login("editor_a")
        access = resp.json()["data"]["access_token"]
        self.assertEqual(self._protected(access).status_code, 200)
        self.client.post(
            "/api/auth/logout-all", headers={"Authorization": f"Bearer {access}"}
        )
        self.assertEqual(self._protected(access).status_code, 401)

    def test_disabled_user_token_invalid(self):
        resp = self.login("editor_a")
        access = resp.json()["data"]["access_token"]
        self.editor.status = UserStatus.disabled.value
        self.db.commit()
        auth_token_service.increment_token_version(self.db, self.editor)
        self.assertEqual(self._protected(access).status_code, 401)


# ──────────────────────────────────────────────────────────────────────────────
# 5. refresh token 轮换与重放
# ──────────────────────────────────────────────────────────────────────────────

class RefreshTokenTests(AuthSecurityBase):
    def test_refresh_rotation(self):
        resp = self.login("admin_a")
        refresh = resp.json()["data"]["refresh_token"]
        self.assertIsNotNone(refresh)

        r1 = self.client.post("/api/auth/refresh", json={"refresh_token": refresh})
        self.assertEqual(r1.status_code, 200)
        new_access = r1.json()["data"]["access_token"]
        self.assertEqual(self._protected(new_access).status_code, 200)

        # 新 refresh 可以继续轮换
        new_refresh = r1.json()["data"]["refresh_token"]
        r2 = self.client.post("/api/auth/refresh", json={"refresh_token": new_refresh})
        self.assertEqual(r2.status_code, 200)

    def test_refresh_replay_detected_and_family_revoked(self):
        resp = self.login("admin_a")
        refresh = resp.json()["data"]["refresh_token"]

        r1 = self.client.post("/api/auth/refresh", json={"refresh_token": refresh})
        self.assertEqual(r1.status_code, 200)
        new_refresh = r1.json()["data"]["refresh_token"]

        # 旧 refresh token 重放 → 401，且整个 family 被撤销
        replay = self.client.post("/api/auth/refresh", json={"refresh_token": refresh})
        self.assertEqual(replay.status_code, 401)

        # family 内新 token 也失效
        after = self.client.post("/api/auth/refresh", json={"refresh_token": new_refresh})
        self.assertEqual(after.status_code, 401)

    def test_invalid_refresh_token(self):
        resp = self.client.post("/api/auth/refresh", json={"refresh_token": "bogus-token"})
        self.assertEqual(resp.status_code, 401)

    def _protected(self, token):
        return self.client.get(
            f"/api/legal/orgs/{self.org_a.id}/cases",
            headers={"Authorization": f"Bearer {token}"},
        )


# ──────────────────────────────────────────────────────────────────────────────
# 6. MFA
# ──────────────────────────────────────────────────────────────────────────────

class MFATests(AuthSecurityBase):
    def _setup_mfa(self, user):
        headers = {"Authorization": f"Bearer {create_access_token({'sub': user.id})}"}
        resp = self.client.post("/api/auth/mfa/setup", headers=headers)
        self.assertEqual(resp.status_code, 200)
        otpauth = resp.json()["data"]["otpauth_uri"]
        secret = otpauth.split("secret=")[1].split("&")[0]
        code = mfa_service.totp_code(secret)
        confirm = self.client.post(f"/api/auth/mfa/confirm?code={code}", headers=headers)
        self.assertEqual(confirm.status_code, 200)
        recovery_codes = confirm.json()["data"]["recovery_codes"]
        return secret, recovery_codes

    def test_mfa_required_at_login(self):
        self._setup_mfa(self.admin)
        resp = self.login("admin_a")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertTrue(data["mfa_required"])
        self.assertIn("challenge", data)
        # 不能直接返回完整 access token
        self.assertNotIn("access_token", data)

    def test_mfa_verify_correct_code(self):
        secret, _ = self._setup_mfa(self.admin)
        login = self.login("admin_a")
        challenge = login.json()["data"]["challenge"]
        code = mfa_service.totp_code(secret)
        verify = self.client.post(f"/api/auth/mfa/verify?challenge={challenge}&code={code}")
        self.assertEqual(verify.status_code, 200)
        access = verify.json()["data"]["access_token"]
        self.assertEqual(
            self.client.get(f"/api/legal/orgs/{self.org_a.id}/cases",
                            headers={"Authorization": f"Bearer {access}"}).status_code,
            200,
        )

    def test_mfa_verify_wrong_code(self):
        secret, _ = self._setup_mfa(self.admin)
        login = self.login("admin_a")
        challenge = login.json()["data"]["challenge"]
        verify = self.client.post(f"/api/auth/mfa/verify?challenge={challenge}&code=000000")
        self.assertEqual(verify.status_code, 400)
        self.assertEqual(verify.json()["error"]["code"], "MFA_INVALID_CODE")

    def test_mfa_expired_challenge(self):
        secret, _ = self._setup_mfa(self.admin)
        login = self.login("admin_a")
        challenge = login.json()["data"]["challenge"]
        row = self.db.query(MFAChallenge).filter(
            MFAChallenge.challenge_jti == challenge
        ).first()
        row.expires_at = _now() - timedelta(minutes=5)
        self.db.commit()
        code = mfa_service.totp_code(secret)
        verify = self.client.post(f"/api/auth/mfa/verify?challenge={challenge}&code={code}")
        self.assertEqual(verify.status_code, 401)

    def test_mfa_recovery_code_single_use(self):
        _, recovery_codes = self._setup_mfa(self.admin)
        code = recovery_codes[0]

        login1 = self.login("admin_a")
        ch1 = login1.json()["data"]["challenge"]
        v1 = self.client.post(f"/api/auth/mfa/verify?challenge={ch1}&code={code}")
        self.assertEqual(v1.status_code, 200)

        login2 = self.login("admin_a")
        ch2 = login2.json()["data"]["challenge"]
        v2 = self.client.post(f"/api/auth/mfa/verify?challenge={ch2}&code={code}")
        self.assertEqual(v2.status_code, 400)

    def test_challenge_cannot_access_business_api(self):
        self._setup_mfa(self.admin)
        login = self.login("admin_a")
        challenge = login.json()["data"]["challenge"]
        # challenge token 不是 access token，不能访问业务接口
        resp = self.client.get(
            f"/api/legal/orgs/{self.org_a.id}/cases",
            headers={"Authorization": f"Bearer {challenge}"},
        )
        self.assertEqual(resp.status_code, 401)


# ──────────────────────────────────────────────────────────────────────────────
# 7. 风险识别（确定性）
# ──────────────────────────────────────────────────────────────────────────────

class RiskAssessmentTests(AuthSecurityBase):
    def test_new_device_is_medium_risk(self):
        risk = auth_token_service.assess_risk(
            self.db, self.admin, device_id="new-device-1",
            ip_address="10.1.1.1", user_agent="ua1",
        )
        self.assertEqual(risk.risk_level, "medium")
        self.assertTrue(risk.requires_mfa)
        self.assertIn("new_device", risk.reasons)

    def test_known_device_ip_change_is_medium(self):
        auth_token_service.record_device(
            self.db, self.admin, device_id="dev-2", ip_address="10.0.0.1",
            user_agent="ua2", risk=auth_token_service.assess_risk(
                self.db, self.admin, device_id="dev-2", ip_address="10.0.0.1", user_agent="ua2"),
        )
        risk = auth_token_service.assess_risk(
            self.db, self.admin, device_id="dev-2", ip_address="10.0.0.99", user_agent="ua2",
        )
        self.assertEqual(risk.risk_level, "medium")
        self.assertIn("ip_changed", risk.reasons)

    def test_known_device_same_context_is_low(self):
        auth_token_service.record_device(
            self.db, self.admin, device_id="dev-3", ip_address="10.0.0.5",
            user_agent="ua3", risk=auth_token_service.assess_risk(
                self.db, self.admin, device_id="dev-3", ip_address="10.0.0.5", user_agent="ua3"),
        )
        risk = auth_token_service.assess_risk(
            self.db, self.admin, device_id="dev-3", ip_address="10.0.0.5", user_agent="ua3",
        )
        self.assertEqual(risk.risk_level, "low")
        self.assertFalse(risk.requires_mfa)

    def test_repeated_failures_high_risk(self):
        risk = auth_token_service.assess_risk(
            self.db, self.admin, device_id="dev-4", ip_address="10.0.0.6",
            user_agent="ua4", login_failures=3,
        )
        self.assertEqual(risk.risk_level, "high")


# ──────────────────────────────────────────────────────────────────────────────
# 8. 长流程权限快照
# ──────────────────────────────────────────────────────────────────────────────

class SnapshotTests(AuthSecurityBase):
    def _capture(self, user, doc_ids=None):
        ctx = self._ctx(user, self.org_a)
        return authorization_service.capture_snapshot(
            self.db, user, ctx, document_ids=doc_ids or [],
        )

    def test_snapshot_valid(self):
        snap_id = self._capture(self.editor)
        snap = authorization_service.assert_snapshot(self.db, snap_id, user_id=self.editor.id)
        self.assertEqual(snap.snapshot_id, snap_id)

    def test_ordinary_role_change_keeps_snapshot(self):
        """普通角色变化（组织内角色调整）不影响已启动流程的快照一致性。"""
        doc = self._doc(self.admin, scope="organization", org=self.org_a)
        self.db.add(DocumentAccessRule(
            document_id=doc.id, subject_type="organization",
            subject_value=str(self.org_a.id), permission="read",
        ))
        self.db.commit()
        snap_id = self._capture(self.editor, doc_ids=[doc.id])

        # 组织角色从 editor 调整为 client（普通变化）
        self.m_editor.legal_role = LegalMemberRole.client.value
        self.db.commit()

        snap = authorization_service.assert_snapshot(self.db, snap_id, user_id=self.editor.id)
        self.assertEqual(snap.legal_role, "editor")  # 快照保持启动时角色

    def test_token_version_bump_terminates_snapshot(self):
        snap_id = self._capture(self.editor)
        auth_token_service.increment_token_version(self.db, self.editor)
        with self.assertRaises(Exception) as raised:
            authorization_service.assert_snapshot(self.db, snap_id, user_id=self.editor.id)
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(raised.exception.detail.get("code"), "TOKEN_VERSION_MISMATCH")

    def test_disabled_user_terminates_snapshot(self):
        snap_id = self._capture(self.editor)
        self.editor.status = UserStatus.disabled.value
        self.db.commit()
        with self.assertRaises(Exception) as raised:
            authorization_service.assert_snapshot(self.db, snap_id, user_id=self.editor.id)
        self.assertEqual(raised.exception.status_code, 403)

    def test_membership_removal_terminates_snapshot(self):
        snap_id = self._capture(self.editor)
        self.db.delete(self.m_editor)
        self.db.commit()
        with self.assertRaises(Exception) as raised:
            authorization_service.assert_snapshot(self.db, snap_id, user_id=self.editor.id)
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(raised.exception.detail.get("code"), "MEMBERSHIP_REVOKED")

    def test_document_auth_revoked_terminates_snapshot(self):
        doc = self._doc(self.admin, scope="restricted")
        self.db.add(DocumentAccessRule(
            document_id=doc.id, subject_type="user",
            subject_value=str(self.editor.id), permission="read",
        ))
        self.db.commit()
        snap_id = self._capture(self.editor, doc_ids=[doc.id])

        # 显式授权被撤销 → 快照终止
        self.db.query(DocumentAccessRule).filter(
            DocumentAccessRule.document_id == doc.id
        ).delete()
        self.db.commit()
        with self.assertRaises(Exception) as raised:
            authorization_service.assert_snapshot(self.db, snap_id, user_id=self.editor.id)
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(raised.exception.detail.get("code"), "DOCUMENT_AUTH_REVOKED")


# ──────────────────────────────────────────────────────────────────────────────
# 9. 客户门户越权（link / item / case / organization）
# ──────────────────────────────────────────────────────────────────────────────

class PortalScopeTests(unittest.TestCase):
    """门户链接是独立能力令牌：客户不能通过修改 item/case/org 访问其他案件。"""

    def setUp(self):
        import hashlib
        import json
        import secrets
        import tempfile
        from pathlib import Path

        from app.models.document import Document
        from app.models.legal import LegalCase
        from app.models.legal_portal import LegalPortalLinkItem
        from app.models.org import Organization, OrganizationMember

        self._tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
        self._tmp.write(b"portal doc")
        self._tmp.close()
        self.tmp_name = self._tmp.name

        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        self.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        self.db = self.SessionLocal()

        self.org_a = Organization(name="OrgA", code="PA")
        self.org_b = Organization(name="OrgB", code="PB")
        self.db.add_all([self.org_a, self.org_b])
        self.db.flush()

        self.admin = User(
            username="p_admin", email="p@test.com",
            hashed_password=hash_password("pw"), role="user", status=UserStatus.active.value,
        )
        self.db.add(self.admin)
        self.db.flush()
        self.db.add(OrganizationMember(
            organization_id=self.org_a.id, user_id=self.admin.id, legal_role="admin"))

        self.case_a = LegalCase(title="案件A", case_type="other",
                                organization_id=self.org_a.id, user_id=self.admin.id)
        self.case_b = LegalCase(title="案件B", case_type="other",
                                organization_id=self.org_b.id, user_id=self.admin.id)
        self.db.add_all([self.case_a, self.case_b])
        self.db.flush()

        self.doc_a = Document(
            user_id=self.admin.id, organization_id=self.org_a.id, title="A案文书",
            file_path=self.tmp_name, file_type="txt", download_enabled=True,
            metadata_json=json.dumps({"case_id": self.case_a.id}),
        )
        self.doc_b = Document(
            user_id=self.admin.id, organization_id=self.org_b.id, title="B案文书",
            file_path=self.tmp_name, file_type="txt", download_enabled=True,
            metadata_json=json.dumps({"case_id": self.case_b.id}),
        )
        self.db.add_all([self.doc_a, self.doc_b])
        self.db.commit()

        # 不要求邮箱验证的链接（绕过 Redis）
        def _make_link(org, case, doc_id, *, status="active"):
            raw = secrets.token_urlsafe(32)
            link = LegalPortalLink(
                organization_id=org.id, case_id=case.id,
                token_hash=hashlib.sha256(raw.encode()).hexdigest(),
                token_prefix=raw[:8], client_email="c@example.com",
                is_permanent=1, require_email_verification=0, aggregate_case=0,
                created_by=self.admin.id, status=status,
            )
            self.db.add(link)
            self.db.flush()
            self.db.add(LegalPortalLinkItem(
                portal_link_id=link.id, item_type="document", item_id=doc_id))
            self.db.commit()
            return raw

        self.raw_a = _make_link(self.org_a, self.case_a, self.doc_a.id)
        self.raw_a_revoked = _make_link(
            self.org_a, self.case_a, self.doc_a.id, status="revoked")

        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self):
        self.db.close()
        app.dependency_overrides.clear()
        from pathlib import Path
        Path(self.tmp_name).unlink(missing_ok=True)

    def test_content_scoped_to_link_case_and_org(self):
        resp = self.client.get(f"/api/legal/portal/{self.raw_a}/content")
        self.assertEqual(resp.status_code, 200)
        docs = resp.json()["data"]["documents"]
        # 只能看到 A 案已发布文书，看不到 B 案文书
        ids = [d["id"] for d in docs]
        self.assertIn(self.doc_a.id, ids)
        self.assertNotIn(self.doc_b.id, ids)

    def test_download_rejects_document_of_another_case(self):
        # 尝试用 A 案的链接下载 B 案文书（同用户创建，但不同案件/组织）
        resp = self.client.get(f"/api/legal/portal/{self.raw_a}/documents/{self.doc_b.id}/download")
        self.assertEqual(resp.status_code, 404)

    def test_download_rejects_unpublished_document(self):
        # 未显式发布到该链接的 A 案文书也应 404
        raw = self.raw_a
        other_a_doc = Document(
            user_id=self.admin.id, organization_id=self.org_a.id, title="A案未发布",
            file_path=self.tmp_name, file_type="txt", download_enabled=True,
            metadata_json='{"case_id": %d}' % self.case_a.id,
        )
        self.db.add(other_a_doc)
        self.db.commit()
        resp = self.client.get(f"/api/legal/portal/{raw}/documents/{other_a_doc.id}/download")
        self.assertEqual(resp.status_code, 404)

    def test_revoked_link_unavailable(self):
        resp = self.client.get(f"/api/legal/portal/{self.raw_a_revoked}/content")
        self.assertEqual(resp.status_code, 404)

    def test_bogus_link_unavailable(self):
        resp = self.client.get(f"/api/legal/portal/not-a-valid-token/content")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
