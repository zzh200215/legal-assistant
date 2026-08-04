"""V3.0 回归测试 — 开放平台：配额限流、合同版本锁定、API密钥 IP 白名单"""
import hashlib
import secrets
import unittest
from unittest.mock import patch, MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import hash_password, create_access_token
from app.core.database import Base, get_db
from app.main import app
from app.models.user import User, UserStatus
from app.models.org import Organization, OrganizationMember
from app.models.legal import LegalCase
from app.models.legal_contract import LegalContract, LegalContractVersion, LegalSignRequest
from app.models.legal_platform import DeveloperApp, DeveloperApiKey
from app.models.subscription import SubscriptionPlan, UserSubscription, SubscriptionStatus
from fastapi.testclient import TestClient


def _make_engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


class ContractVersionLockTests(unittest.TestCase):
    """合同版本锁定：已签署/签署中的版本不可新建版本（CONTRACT_VERSION_LOCKED 409）"""

    def setUp(self):
        self.engine = _make_engine()
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session()

        org = Organization(name="ContractOrg", code="CORG")
        self.db.add(org)
        self.db.flush()

        self.user = User(
            username="contractadmin",
            email="cadmin@t.com",
            hashed_password=hash_password("pw"),
            role="user",
            status=UserStatus.active.value,
            organization_id=org.id,
        )
        self.db.add(self.user)
        self.db.flush()
        self.db.add(OrganizationMember(
            organization_id=org.id, user_id=self.user.id, legal_role="admin"))
        self.db.flush()

        case = LegalCase(
            title="ContractCase",
            case_type="other",
            organization_id=org.id,
            user_id=self.user.id,
        )
        self.db.add(case)
        self.db.flush()

        self.org_id = org.id
        self.contract = LegalContract(
            organization_id=org.id,
            case_id=case.id,
            title="TestContract",
            contract_no="CONT-001",
            status="active",
            created_by=self.user.id,
        )
        self.db.add(self.contract)
        self.db.commit()

        def _override_db():
            yield self.db

        app.dependency_overrides[get_db] = _override_db
        token = create_access_token({"sub": str(self.user.id)})
        self.client = TestClient(app, raise_server_exceptions=False)
        self.headers = {"Authorization": f"Bearer {token}"}

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def _add_version(self, parse_status: str = "ready") -> LegalContractVersion:
        ver = LegalContractVersion(
            contract_id=self.contract.id,
            organization_id=self.org_id,
            version_no=1,
            parse_status=parse_status,
            created_by=self.user.id,
        )
        self.db.add(ver)
        self.db.commit()
        self.db.refresh(ver)
        return ver

    def _add_sign_request(self, version_id: int, status: str) -> LegalSignRequest:
        sr = LegalSignRequest(
            contract_id=self.contract.id,
            contract_version_id=version_id,
            organization_id=self.org_id,
            provider="fadada",
            status=status,
            initiated_by=self.user.id,
        )
        self.db.add(sr)
        self.db.commit()
        self.db.refresh(sr)
        return sr

    def test_can_add_version_when_no_sign_request(self):
        resp = self.client.post(
            f"/api/legal/contracts/{self.contract.id}/versions",
            json={"version_label": "v1", "text_snapshot": "合同内容..."},
            headers=self.headers,
        )
        self.assertIn(resp.status_code, (200, 201))

    def test_cannot_add_version_when_signed(self):
        ver = self._add_version()
        self._add_sign_request(ver.id, "signed")

        resp = self.client.post(
            f"/api/legal/contracts/{self.contract.id}/versions",
            json={"version_label": "v2", "text_snapshot": "修改版合同..."},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 409)
        self.assertIn("CONTRACT_VERSION_LOCKED", resp.text)

    def test_cannot_add_version_when_pending_sign(self):
        ver = self._add_version()
        self._add_sign_request(ver.id, "pending_sign")

        resp = self.client.post(
            f"/api/legal/contracts/{self.contract.id}/versions",
            json={"version_label": "v2", "text_snapshot": "修改版合同..."},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 409)

    def test_can_add_version_when_sign_request_rejected(self):
        """拒签后可以创建新版本（应走修订流程）"""
        ver = self._add_version()
        self._add_sign_request(ver.id, "rejected")

        resp = self.client.post(
            f"/api/legal/contracts/{self.contract.id}/versions",
            json={"version_label": "v2", "text_snapshot": "修改版合同..."},
            headers=self.headers,
        )
        # rejected 不算锁定，允许创建新版本
        self.assertNotEqual(resp.status_code, 409)


class OpenApiRateLimitTests(unittest.TestCase):
    """Open API 限流：免费套餐100次/天，团队套餐无限"""

    def setUp(self):
        self.engine = _make_engine()
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session()

        org = Organization(name="ApiOrg", code="APIG")
        self.db.add(org)
        self.db.flush()

        self.admin_user = User(
            username="apiadmin",
            email="apiadmin@t.com",
            hashed_password=hash_password("pw"),
            role="user",
            status=UserStatus.active.value,
            organization_id=org.id,
        )
        self.db.add(self.admin_user)
        self.db.flush()
        self.db.add(OrganizationMember(
            organization_id=org.id, user_id=self.admin_user.id, legal_role="admin"))
        self.db.flush()

        self.org_id = org.id

        # 创建 developer_app 和 api_key
        self.raw_key = "lzj_op_" + secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(self.raw_key.encode()).hexdigest()
        self.dev_app = DeveloperApp(
            organization_id=org.id,
            name="TestApp",
            created_by=self.admin_user.id,
        )
        self.db.add(self.dev_app)
        self.db.flush()
        self.db.add(DeveloperApiKey(
            app_id=self.dev_app.id,
            organization_id=org.id,
            key_hash=key_hash,
            key_prefix=self.raw_key[:16],
        ))
        self.db.commit()

        def _override_db():
            yield self.db

        app.dependency_overrides[get_db] = _override_db
        self.client = TestClient(app, raise_server_exceptions=False)
        self.api_headers = {"X-API-Key": self.raw_key}

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def _mock_redis_at_count(self, count: int):
        mock_r = MagicMock()
        mock_r.incr.return_value = count
        mock_r.expire.return_value = True
        return mock_r

    def test_free_plan_allows_within_limit(self):
        """免费套餐：第100次仍允许"""
        mock_r = self._mock_redis_at_count(100)
        with patch("redis.from_url", return_value=mock_r):
            resp = self.client.post(
                "/api/open/v1/contract-reviews",
                json={"title": "合同审查", "content": "这是一份合同内容，需要审查风险条款。"},
                headers=self.api_headers,
            )
        self.assertNotEqual(resp.status_code, 429)

    def test_free_plan_blocks_over_limit(self):
        """免费套餐：第101次应返回429"""
        mock_r = self._mock_redis_at_count(101)
        with patch("redis.from_url", return_value=mock_r):
            resp = self.client.post(
                "/api/open/v1/contract-reviews",
                json={"title": "合同审查", "content": "这是一份合同内容，需要审查风险条款。"},
                headers=self.api_headers,
            )
        self.assertEqual(resp.status_code, 429)

    def test_team_plan_has_fixed_limit(self):
        """团队套餐必须有合同约定的固定上限，不能使用无限调用语义。"""
        # 为管理员添加团队订阅
        plan = SubscriptionPlan(
            tier="team",
            name="团队版",
            price_monthly=999,
            quota_consultation=5000,
            quota_review=2000,
            quota_draft=2000,
        )
        self.db.add(plan)
        self.db.flush()
        self.db.add(UserSubscription(
            user_id=self.admin_user.id,
            plan_id=plan.id,
            status=SubscriptionStatus.active.value,
        ))
        self.db.commit()

        mock_r = self._mock_redis_at_count(9999)
        with patch("redis.from_url", return_value=mock_r):
            resp = self.client.post(
                "/api/open/v1/contract-reviews",
                json={"title": "合同审查", "content": "这是一份合同内容，需要审查风险条款。"},
                headers=self.api_headers,
            )
        self.assertEqual(resp.status_code, 429)

    def test_invalid_api_key_returns_403(self):
        resp = self.client.post(
            "/api/open/v1/contract-reviews",
            json={"title": "合同审查", "content": "这是一份合同内容，需要审查风险条款。"},
            headers={"X-API-Key": "lzj_op_invalid_key"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_ip_whitelist_blocks_unlisted_ip(self):
        """IP 白名单：来源IP不在列表内应返回403"""
        import json
        self.dev_app.ip_whitelist_json = json.dumps(["192.168.1.0/24"])
        self.db.commit()

        mock_r = self._mock_redis_at_count(1)
        with patch("redis.from_url", return_value=mock_r):
            resp = self.client.post(
                "/api/open/v1/contract-reviews",
                json={"title": "合同审查", "content": "这是一份合同内容，需要审查风险条款。"},
                headers=self.api_headers,
            )
        # testclient IP 为 testclient (非 192.168.1.x)
        self.assertEqual(resp.status_code, 403)
        self.assertIn("API_KEY_IP_DENIED", resp.text)

    def test_quota_deduction_on_review_creation(self):
        """创建Open API审查任务后，管理员月度review_count应加1"""
        from app.models.subscription import QuotaUsage
        from datetime import datetime

        mock_r = self._mock_redis_at_count(1)
        with patch("redis.from_url", return_value=mock_r):
            resp = self.client.post(
                "/api/open/v1/contract-reviews",
                json={"title": "配额测试", "content": "这是一份合同内容，需要审查风险条款。"},
                headers=self.api_headers,
            )
        self.assertIn(resp.status_code, (200, 201))

        year_month = datetime.utcnow().strftime("%Y-%m")
        usage = self.db.query(QuotaUsage).filter(
            QuotaUsage.user_id == self.admin_user.id,
            QuotaUsage.year_month == year_month,
        ).first()
        self.assertIsNotNone(usage)
        self.assertEqual(usage.review_count, 1)


if __name__ == "__main__":
    unittest.main()
