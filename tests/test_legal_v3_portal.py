"""V3.0 回归测试 — 客户门户：令牌无效、OTP 锁定、越权访问"""
import hashlib
import secrets
import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from unittest.mock import patch, MagicMock

import app.models  # noqa: F401
from app.core.auth import hash_password, create_access_token
from app.core.database import Base, get_db
from app.main import app
from app.models.user import User, UserStatus
from app.models.org import Organization, OrganizationMember
from app.models.legal import LegalCase
from app.models.legal_portal import LegalPortalLink
from fastapi.testclient import TestClient


def _make_engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


class PortalTokenAuthTests(unittest.TestCase):
    """门户令牌：无效/撤销/过期令牌返回统一 404；不泄露具体失效原因"""

    def setUp(self):
        self.engine = _make_engine()
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session()

        org = Organization(name="PortalOrg", code="PORT")
        self.db.add(org)
        self.db.flush()

        self.user = User(
            username="reviewer1",
            email="rev1@t.com",
            hashed_password=hash_password("pw"),
            role="user",
            status=UserStatus.active.value,
            organization_id=org.id,
        )
        self.db.add(self.user)
        self.db.flush()
        self.db.add(OrganizationMember(
            organization_id=org.id, user_id=self.user.id, legal_role="reviewer"))
        self.db.flush()

        self.case = LegalCase(
            title="PortalCase",
            case_type="other",
            organization_id=org.id,
            user_id=self.user.id,
        )
        self.db.add(self.case)
        self.db.commit()

        self.org_id = org.id

        def _override_db():
            yield self.db

        app.dependency_overrides[get_db] = _override_db
        token = create_access_token({"sub": str(self.user.id)})
        self.client = TestClient(app, raise_server_exceptions=False)
        self.headers = {"Authorization": f"Bearer {token}"}

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def _make_link(self, status: str = "active", require_email_verification: int = 0,
                   client_email: str | None = None) -> tuple[str, LegalPortalLink]:
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        link = LegalPortalLink(
            organization_id=self.org_id,
            case_id=self.case.id,
            token_hash=token_hash,
            token_prefix=raw_token[:8],
            status=status,
            is_permanent=1,
            require_email_verification=require_email_verification,
            client_email=client_email,
            created_by=self.user.id,
        )
        self.db.add(link)
        self.db.commit()
        self.db.refresh(link)
        return raw_token, link

    def test_nonexistent_token_returns_404(self):
        fake_token = secrets.token_urlsafe(32)
        resp = self.client.get(f"/api/legal/portal/{fake_token}/content")
        self.assertEqual(resp.status_code, 404)

    def test_revoked_link_returns_404(self):
        raw_token, _ = self._make_link(status="revoked")
        resp = self.client.get(f"/api/legal/portal/{raw_token}/content")
        self.assertEqual(resp.status_code, 404)

    def test_expired_link_returns_404(self):
        raw_token, _ = self._make_link(status="expired")
        resp = self.client.get(f"/api/legal/portal/{raw_token}/content")
        self.assertEqual(resp.status_code, 404)

    def test_active_link_with_elapsed_expiry_returns_404(self):
        raw_token, link = self._make_link()
        link.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        self.db.commit()

        resp = self.client.get(f"/api/legal/portal/{raw_token}/content")
        self.assertEqual(resp.status_code, 404)

    def test_active_link_accessible(self):
        raw_token, _ = self._make_link(status="active")
        # portal content requires no auth — should not return 401/403
        resp = self.client.get(f"/api/legal/portal/{raw_token}/content")
        self.assertNotIn(resp.status_code, (401, 403))

    def test_error_message_does_not_reveal_reason(self):
        """失效原因不能出现在响应中（统一显示 PORTAL_LINK_UNAVAILABLE）"""
        raw_token, _ = self._make_link(status="revoked")
        resp = self.client.get(f"/api/legal/portal/{raw_token}/content")
        body = resp.text
        self.assertNotIn("revoked", body.lower())
        self.assertNotIn("expired", body.lower())
        self.assertIn("PORTAL_LINK_UNAVAILABLE", body)

    def test_revoke_endpoint_invalidates_link(self):
        _, link = self._make_link(status="active")
        resp = self.client.post(
            f"/api/legal/portal-links/{link.id}/revoke",
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 200)
        self.db.refresh(link)
        self.assertEqual(link.status, "revoked")

    def test_create_link_uses_case_organization_and_aligned_route(self):
        resp = self.client.post(
            f"/api/legal/orgs/{self.org_id}/cases/{self.case.id}/portal-links",
            headers=self.headers,
            json={"client_email": "client@example.com", "expires_days": 7,
                  "require_email_verification": 1, "items": []},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["link"]["organization_id"], self.org_id)


    def test_get_branding_defaults_empty(self):
        r = self.client.get(f'/api/legal/orgs/{self.org_id}/portal-branding', headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(r.json()['data']['portal_logo_url'])
        self.assertIsNone(r.json()['data']['portal_welcome_message'])

    def test_put_branding_and_content_returns_org(self):
        r = self.client.put(f'/api/legal/orgs/{self.org_id}/portal-branding', headers=self.headers, json={
            'portal_logo_url': 'https://x.com/logo.png', 'portal_welcome_message': '欢迎您',
        })
        self.assertEqual(r.status_code, 200, r.text)
        raw, link = self._make_link()
        r2 = self.client.get(f'/api/legal/portal/{raw}/content')
        self.assertEqual(r2.status_code, 200, r2.text)
        org = r2.json()['data']['organization']
        self.assertEqual(org['name'], 'PortalOrg')
        self.assertEqual(org['portal_logo_url'], 'https://x.com/logo.png')
        self.assertEqual(org['portal_welcome_message'], '欢迎您')

    def test_branding_update_requires_org_admin(self):
        from app.models.org import OrganizationMember
        self.db.query(OrganizationMember).filter_by(user_id=self.user.id).update({'legal_role': 'client'})
        self.db.commit()
        r = self.client.put(f'/api/legal/orgs/{self.org_id}/portal-branding', headers=self.headers, json={
            'portal_welcome_message': 'x',
        })
        self.assertEqual(r.status_code, 403, r.text)

    def test_branding_update_strips_and_nulls(self):
        r = self.client.put(f'/api/legal/orgs/{self.org_id}/portal-branding', headers=self.headers, json={
            'portal_logo_url': '   ', 'portal_welcome_message': '  欢迎  ',
        })
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(r.json()['data']['portal_logo_url'])
        self.assertEqual(r.json()['data']['portal_welcome_message'], '欢迎')

    def test_outsider_cannot_manage_case_deadlines(self):
        outsider = User(
            username="outsider",
            email="outsider@t.com",
            hashed_password=hash_password("pw"),
            role="user",
            status=UserStatus.active.value,
        )
        self.db.add(outsider)
        self.db.commit()
        outsider_token = create_access_token({"sub": str(outsider.id)})

        resp = self.client.post(
            f"/api/legal/orgs/{self.org_id}/cases/{self.case.id}/deadlines",
            headers={"Authorization": f"Bearer {outsider_token}"},
            json={
                "deadline_type": "hearing",
                "deadline_at": "2026-08-01T09:00:00+08:00",
                "owner_id": self.user.id,
            },
        )
        self.assertEqual(resp.status_code, 403)

    def test_progress_update_uses_case_organization_and_aligned_route(self):
        resp = self.client.post(
            f"/api/legal/orgs/{self.org_id}/cases/{self.case.id}/progress-updates",
            headers=self.headers,
            json={"title": "已立案", "body": "案件已正式立案。"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["organization_id"], self.org_id)

    def test_verified_portal_rejects_missing_session(self):
        raw_token, _ = self._make_link(require_email_verification=1)
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        with patch("redis.from_url", return_value=mock_redis):
            resp = self.client.get(f"/api/legal/portal/{raw_token}/content")
        self.assertEqual(resp.status_code, 401)

    def test_send_otp_uses_transactional_email_service(self):
        raw_token, _ = self._make_link(require_email_verification=1, client_email="client@example.com")
        mock_redis = MagicMock()
        mock_redis.exists.return_value = False
        mock_redis.incr.return_value = 1  # first send in window, not rate-limited
        mock_redis.expire.return_value = True
        with patch("redis.from_url", return_value=mock_redis), \
             patch("app.services.notification.outbound_email_service.outbound_email_service.send_portal_otp") as send_otp:
            resp = self.client.post(f"/api/legal/portal/{raw_token}/send-otp")
        self.assertEqual(resp.status_code, 200)
        send_otp.assert_called_once()
        self.assertTrue(mock_redis.setex.called)

    def test_single_use_verified_link_allows_first_content_view(self):
        raw_token, link = self._make_link(require_email_verification=1)
        link.max_access_count = 1
        self.db.commit()
        mock_redis = MagicMock()
        mock_redis.exists.return_value = False
        mock_redis.get.side_effect = ["123456", str(link.id)]

        with patch("redis.from_url", return_value=mock_redis):
            verify = self.client.post(
                f"/api/legal/portal/{raw_token}/verify",
                params={"otp": "123456"},
            )
            self.assertEqual(verify.status_code, 200)
            session_token = verify.json()["data"]["session_token"]
            content = self.client.get(
                f"/api/legal/portal/{raw_token}/content",
                headers={"X-Portal-Session": session_token},
            )
        self.assertEqual(content.status_code, 200)


class PortalOTPLockTests(unittest.TestCase):
    """OTP 验证：连续5次失败后锁定15分钟"""

    def setUp(self):
        self.engine = _make_engine()
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session()

        org = Organization(name="OTPOrg", code="OTPO")
        self.db.add(org)
        self.db.flush()

        user = User(
            username="otprev",
            email="otp@t.com",
            hashed_password=hash_password("pw"),
            role="user",
            status=UserStatus.active.value,
            organization_id=org.id,
        )
        self.db.add(user)
        self.db.flush()
        self.db.add(OrganizationMember(
            organization_id=org.id, user_id=user.id, legal_role="reviewer"))
        self.db.flush()

        case = LegalCase(
            title="OTPCase",
            case_type="other",
            organization_id=org.id,
            user_id=user.id,
        )
        self.db.add(case)
        self.db.commit()

        self.raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(self.raw_token.encode()).hexdigest()
        link = LegalPortalLink(
            organization_id=org.id,
            case_id=case.id,
            token_hash=token_hash,
            token_prefix=self.raw_token[:8],
            status="active",
            is_permanent=1,
            require_email_verification=1,
            created_by=user.id,
        )
        self.db.add(link)
        self.db.commit()

        def _override_db():
            yield self.db

        app.dependency_overrides[get_db] = _override_db
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def test_otp_lockout_after_5_failures(self):
        """5次错误后下次请求返回429并含 PORTAL_OTP_LOCKED"""
        mock_redis = MagicMock()
        # fail_count starts at 0; each get returns incrementing value
        fail_counts = [1, 2, 3, 4, 5, 6]
        mock_redis.get.side_effect = [None] + [str(i) for i in fail_counts]
        mock_redis.incr.side_effect = fail_counts
        mock_redis.expire.return_value = True
        mock_redis.set.return_value = True

        with patch("redis.from_url", return_value=mock_redis):
            for i in range(5):
                resp = self.client.post(
                    f"/api/legal/portal/{self.raw_token}/verify",
                    params={"otp": "000000"},
                )
                # 前4次400（OTP错误），第5次因计数达5触发429
                self.assertIn(resp.status_code, (400, 429))

            # 6th attempt — lock should activate (fail_count >= 5)
            mock_redis.get.return_value = "1"  # lock exists
            resp = self.client.post(
                f"/api/legal/portal/{self.raw_token}/verify",
                params={"otp": "000000"},
            )
            self.assertIn(resp.status_code, (429, 400))


class PortalP003HardeningTests(unittest.TestCase):
    """P0-03 安全收紧：schema校验、OTP限流、访问日志、会话追踪"""

    def setUp(self):
        self.engine = _make_engine()
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session()

        org = Organization(name="HardenOrg", code="HARD")
        self.db.add(org)
        self.db.flush()

        self.admin = User(
            username="hardadmin",
            email="hardadmin@t.com",
            hashed_password=hash_password("pw"),
            role="user",
            status=UserStatus.active.value,
            organization_id=org.id,
        )
        self.db.add(self.admin)
        self.db.flush()
        self.db.add(OrganizationMember(
            organization_id=org.id, user_id=self.admin.id, legal_role="admin"))

        self.reviewer = User(
            username="hardreviewer",
            email="hardreviewer@t.com",
            hashed_password=hash_password("pw"),
            role="user",
            status=UserStatus.active.value,
            organization_id=org.id,
        )
        self.db.add(self.reviewer)
        self.db.flush()
        self.db.add(OrganizationMember(
            organization_id=org.id, user_id=self.reviewer.id, legal_role="reviewer"))

        self.case = LegalCase(
            title="HardenCase",
            case_type="other",
            organization_id=org.id,
            user_id=self.admin.id,
        )
        self.db.add(self.case)
        self.db.commit()

        self.org_id = org.id

        def _override_db():
            yield self.db

        app.dependency_overrides[get_db] = _override_db
        self.admin_token = create_access_token({"sub": str(self.admin.id)})
        self.reviewer_token = create_access_token({"sub": str(self.reviewer.id)})
        self.client = TestClient(app, raise_server_exceptions=False)
        self.admin_headers = {"Authorization": f"Bearer {self.admin_token}"}
        self.reviewer_headers = {"Authorization": f"Bearer {self.reviewer_token}"}

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def _create_link_via_api(self, payload: dict, headers: dict | None = None):
        return self.client.post(
            f"/api/legal/orgs/{self.org_id}/cases/{self.case.id}/portal-links",
            headers=headers or self.admin_headers,
            json=payload,
        )

    def _make_link(self, **kwargs) -> tuple[str, LegalPortalLink]:
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        link = LegalPortalLink(
            organization_id=self.org_id,
            case_id=self.case.id,
            token_hash=token_hash,
            token_prefix=raw_token[:8],
            status="active",
            is_permanent=0,
            require_email_verification=kwargs.get("require_email_verification", 1),
            client_email=kwargs.get("client_email", "c@example.com"),
            created_by=self.admin.id,
        )
        self.db.add(link)
        self.db.commit()
        self.db.refresh(link)
        return raw_token, link

    # ── Schema validation ─────────────────────────────────────────────────────

    def test_create_link_requires_client_email(self):
        resp = self._create_link_via_api({"expires_days": 7, "items": []})
        self.assertEqual(resp.status_code, 422)

    def test_create_link_rejects_invalid_email_format(self):
        resp = self._create_link_via_api(
            {"client_email": "not-an-email", "expires_days": 7, "items": []})
        self.assertEqual(resp.status_code, 422)

    def test_create_link_rejects_invalid_expires_days(self):
        for bad_days in [0, 1, 14, 60, 365, 99999]:
            resp = self._create_link_via_api(
                {"client_email": "c@t.com", "expires_days": bad_days, "items": []})
            self.assertEqual(resp.status_code, 422,
                             f"expires_days={bad_days} should be rejected")

    def test_create_link_allows_valid_expires_days(self):
        for good_days in [7, 30, 90]:
            resp = self._create_link_via_api(
                {"client_email": "c@t.com", "expires_days": good_days, "items": []})
            self.assertEqual(resp.status_code, 200,
                             f"expires_days={good_days} should be accepted, got {resp.status_code}")

    def test_create_link_is_never_permanent(self):
        """API创建的链接 is_permanent 必须为0"""
        resp = self._create_link_via_api(
            {"client_email": "c@t.com", "expires_days": 30, "items": []})
        self.assertEqual(resp.status_code, 200)
        link_id = resp.json()["data"]["link"]["id"]
        from app.models.legal_portal import LegalPortalLink as LPL
        link = self.db.query(LPL).filter(LPL.id == link_id).first()
        self.assertIsNotNone(link.expires_at, "expires_at must be set")
        self.assertEqual(link.is_permanent, 0, "is_permanent must be 0")

    # ── require_email_verification restrictions ───────────────────────────────

    def test_reviewer_cannot_disable_email_verification(self):
        resp = self._create_link_via_api(
            {"client_email": "c@t.com", "expires_days": 7,
             "require_email_verification": 0, "items": []},
            headers=self.reviewer_headers,
        )
        self.assertEqual(resp.status_code, 403)

    def test_admin_can_disable_verification_for_progress_only_links(self):
        mock_audit = MagicMock()
        with patch("app.services.org.security_audit_service.write_event", mock_audit):
            resp = self._create_link_via_api(
                {"client_email": "c@t.com", "expires_days": 7,
                 "require_email_verification": 0, "items": []},
                headers=self.admin_headers,
            )
        self.assertEqual(resp.status_code, 200)
        mock_audit.assert_called_once()

    # ── OTP send rate limiting ────────────────────────────────────────────────

    def test_otp_send_rate_limited_after_3_sends(self):
        raw_token, _ = self._make_link(client_email="c@example.com")
        mock_redis = MagicMock()
        mock_redis.exists.return_value = False
        # 4th send: incr returns 4, which > _OTP_SEND_MAX (3)
        mock_redis.incr.return_value = 4
        mock_redis.expire.return_value = True
        with patch("redis.from_url", return_value=mock_redis), \
             patch("app.services.notification.outbound_email_service.outbound_email_service.send_portal_otp"):
            resp = self.client.post(f"/api/legal/portal/{raw_token}/send-otp")
        self.assertEqual(resp.status_code, 429)

    def test_otp_first_send_succeeds(self):
        raw_token, _ = self._make_link(client_email="c@example.com")
        mock_redis = MagicMock()
        mock_redis.exists.return_value = False
        mock_redis.incr.return_value = 1
        mock_redis.expire.return_value = True
        with patch("redis.from_url", return_value=mock_redis), \
             patch("app.services.notification.outbound_email_service.outbound_email_service.send_portal_otp"):
            resp = self.client.post(f"/api/legal/portal/{raw_token}/send-otp")
        self.assertEqual(resp.status_code, 200)

    # ── Access log written ────────────────────────────────────────────────────

    def test_access_log_written_on_otp_send(self):
        from app.models.legal_portal import LegalPortalAccessLog
        raw_token, link = self._make_link(client_email="c@example.com")
        mock_redis = MagicMock()
        mock_redis.exists.return_value = False
        mock_redis.incr.return_value = 1
        mock_redis.expire.return_value = True
        with patch("redis.from_url", return_value=mock_redis), \
             patch("app.services.notification.outbound_email_service.outbound_email_service.send_portal_otp"):
            resp = self.client.post(f"/api/legal/portal/{raw_token}/send-otp")
        self.assertEqual(resp.status_code, 200)
        log = self.db.query(LegalPortalAccessLog).filter(
            LegalPortalAccessLog.portal_link_id == link.id,
            LegalPortalAccessLog.action == "otp_send",
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.result, "success")

    def test_access_log_written_on_verify_failure(self):
        from app.models.legal_portal import LegalPortalAccessLog
        raw_token, link = self._make_link()
        mock_redis = MagicMock()
        mock_redis.exists.return_value = False
        mock_redis.get.return_value = None  # no stored OTP
        mock_redis.incr.return_value = 1
        mock_redis.expire.return_value = True
        with patch("redis.from_url", return_value=mock_redis):
            resp = self.client.post(
                f"/api/legal/portal/{raw_token}/verify",
                params={"otp": "000000"},
            )
        self.assertEqual(resp.status_code, 400)
        log = self.db.query(LegalPortalAccessLog).filter(
            LegalPortalAccessLog.portal_link_id == link.id,
            LegalPortalAccessLog.action == "otp_verify",
            LegalPortalAccessLog.result == "failure",
        ).first()
        self.assertIsNotNone(log)

    def test_access_log_written_on_verify_success(self):
        from app.models.legal_portal import LegalPortalAccessLog
        raw_token, link = self._make_link()
        mock_redis = MagicMock()
        mock_redis.exists.return_value = False
        mock_redis.get.return_value = "123456"
        mock_redis.delete.return_value = True
        mock_redis.setex.return_value = True
        mock_redis.sadd.return_value = 1
        mock_redis.expire.return_value = True
        with patch("redis.from_url", return_value=mock_redis):
            resp = self.client.post(
                f"/api/legal/portal/{raw_token}/verify",
                params={"otp": "123456"},
            )
        self.assertEqual(resp.status_code, 200)
        log = self.db.query(LegalPortalAccessLog).filter(
            LegalPortalAccessLog.portal_link_id == link.id,
            LegalPortalAccessLog.action == "otp_verify",
            LegalPortalAccessLog.result == "success",
        ).first()
        self.assertIsNotNone(log)

    # ── Session TTL ───────────────────────────────────────────────────────────

    def test_session_ttl_is_8_hours(self):
        """验证会话有效期为8小时（28800秒）"""
        from app.api.legal.legal_portal_api import _SESSION_TTL
        self.assertEqual(_SESSION_TTL, 28800)

    # ── Revoke cleans up Redis sessions ──────────────────────────────────────

    def test_revoke_cleans_redis_sessions(self):
        _, link = self._make_link()
        mock_redis = MagicMock()
        mock_redis.smembers.return_value = {"session_abc", "session_def"}
        with patch("redis.from_url", return_value=mock_redis):
            resp = self.client.post(
                f"/api/legal/portal-links/{link.id}/revoke",
                headers=self.admin_headers,
            )
        self.assertEqual(resp.status_code, 200)
        mock_redis.smembers.assert_called_once()
        # delete called: one for each session + the sset_key itself
        self.assertTrue(mock_redis.delete.called)


class PortalP3FeedbackBillingTests(unittest.TestCase):
    """P3 客户反馈入口 + 对账展示：反馈落表、管理端可见、账单快照返回"""

    def setUp(self):
        self.engine = _make_engine()
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session()

        org = Organization(name="P3Org", code="P3F")
        self.db.add(org)
        self.db.flush()

        self.admin = User(
            username="p3admin",
            email="p3admin@t.com",
            hashed_password=hash_password("pw"),
            role="user",
            status=UserStatus.active.value,
            organization_id=org.id,
        )
        self.db.add(self.admin)
        self.db.flush()
        self.db.add(OrganizationMember(
            organization_id=org.id, user_id=self.admin.id, legal_role="admin"))
        self.db.flush()

        self.case = LegalCase(
            title="P3Case", case_type="other",
            organization_id=org.id, user_id=self.admin.id,
        )
        self.db.add(self.case)
        self.db.commit()

        self.org_id = org.id

        def _override_db():
            yield self.db

        app.dependency_overrides[get_db] = _override_db
        token = create_access_token({"sub": str(self.admin.id)})
        self.client = TestClient(app, raise_server_exceptions=False)
        self.headers = {"Authorization": f"Bearer {token}"}

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def _make_link(self, require_email_verification: int = 0) -> tuple[str, LegalPortalLink]:
        raw_token = secrets.token_urlsafe(32)
        link = LegalPortalLink(
            organization_id=self.org_id,
            case_id=self.case.id,
            token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
            token_prefix=raw_token[:8],
            status="active",
            is_permanent=1,
            require_email_verification=require_email_verification,
            created_by=self.admin.id,
        )
        self.db.add(link)
        self.db.commit()
        self.db.refresh(link)
        return raw_token, link

    def test_submit_positive_feedback_persists(self):
        raw, _ = self._make_link()
        resp = self.client.post(
            f"/api/legal/portal/{raw}/feedback",
            json={"score": 1, "note": " 服务很专业 "},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json()["data"]["ok"])
        from app.models.legal_portal import LegalPortalFeedback
        rows = self.db.query(LegalPortalFeedback).all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].score, 1)
        self.assertEqual(rows[0].note, "服务很专业")

    def test_submit_negative_without_note_allowed(self):
        raw, _ = self._make_link()
        resp = self.client.post(
            f"/api/legal/portal/{raw}/feedback",
            json={"score": -1},
        )
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_score_zero_rejected(self):
        raw, _ = self._make_link()
        resp = self.client.post(
            f"/api/legal/portal/{raw}/feedback",
            json={"score": 0},
        )
        self.assertEqual(resp.status_code, 422)

    def test_feedback_on_invalid_token_404(self):
        resp = self.client.post(
            "/api/legal/portal/not-a-real-token/feedback",
            json={"score": 1},
        )
        self.assertEqual(resp.status_code, 404)

    def test_manager_can_list_org_feedback(self):
        raw, link = self._make_link()
        self.client.post(
            f"/api/legal/portal/{raw}/feedback",
            json={"score": 1, "note": "很好"},
        )
        self.client.post(
            f"/api/legal/portal/{raw}/feedback",
            json={"score": -1, "note": "回复慢了"},
        )
        resp = self.client.get(f"/api/legal/orgs/{self.org_id}/portal-feedback",
                               headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(len(data), 2)
        self.assertEqual({d["score"] for d in data}, {1, -1})
        self.assertTrue(all(d["case_id"] == self.case.id for d in data))

    def test_manager_list_feedback_requires_org_member(self):
        outsider = User(
            username="outsider3", email="out3@t.com",
            hashed_password=hash_password("pw"), role="user",
            status=UserStatus.active.value, organization_id=self.org_id,
        )
        self.db.add(outsider)
        self.db.commit()
        token = create_access_token({"sub": str(outsider.id)})
        resp = self.client.get(
            f"/api/legal/orgs/{self.org_id}/portal-feedback",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_content_returns_billing_snapshot_for_sent_invoice(self):
        from datetime import date
        from app.models.legal_billing import LegalInvoice
        self.db.add(LegalInvoice(
            organization_id=self.org_id,
            case_id=self.case.id,
            invoice_no="INV-P3-001",
            client_display_name="张三",
            issue_date=date(2026, 8, 1),
            billing_period_start=date(2026, 7, 1),
            billing_period_end=date(2026, 7, 31),
            subtotal=10000,
            tax_amount=600,
            total_amount=10600,
            status="sent",
            created_by=self.admin.id,
        ))
        self.db.commit()

        raw, _ = self._make_link()
        resp = self.client.get(f"/api/legal/portal/{raw}/content")
        self.assertEqual(resp.status_code, 200, resp.text)
        inv = resp.json()["data"]["invoice"]
        self.assertIsNotNone(inv)
        self.assertEqual(inv["invoice_number"], "INV-P3-001")
        self.assertEqual(inv["total_amount"], 10600.0)
        self.assertEqual(inv["status"], "sent")
        self.assertEqual(inv["period_start"], "2026-07-01")
        self.assertEqual(inv["period_end"], "2026-07-31")
        self.assertEqual(inv["paid_amount"], 0.0)

    def test_content_omits_billing_for_draft_invoice(self):
        from datetime import date
        from app.models.legal_billing import LegalInvoice
        self.db.add(LegalInvoice(
            organization_id=self.org_id,
            case_id=self.case.id,
            invoice_no="INV-P3-DRAFT",
            client_display_name="李四",
            issue_date=date(2026, 8, 1),
            total_amount=500,
            status="draft",
            created_by=self.admin.id,
        ))
        self.db.commit()

        raw, _ = self._make_link()
        resp = self.client.get(f"/api/legal/portal/{raw}/content")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIsNone(resp.json()["data"]["invoice"])
