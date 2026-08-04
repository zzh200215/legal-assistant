"""V3.0 回归测试 — P0-04 电子签署非外部依赖部分：
前置校验（合同状态/版本状态/签署方）、审批链闸门、回调异常时序检测、证据查询。
"""
import hashlib
import hmac
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

import app.models  # noqa: F401
from app.core.auth import hash_password, create_access_token
from app.core.database import Base, get_db
from app.main import app
from app.models.user import User, UserStatus
from app.models.org import Organization, OrganizationMember
from app.models.legal import LegalCase, LegalApprovalChain
from app.models.legal_contract import LegalContract, LegalContractVersion, LegalSignRequest, LegalSignParty
from app.services.signing_provider_service import SigningDispatch

WEBHOOK_SECRET = "test-secret-key"


def _make_engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


class SigningPreflightTests(unittest.TestCase):
    """create_sign_request / send_sign_request 前置校验"""

    def setUp(self):
        self.engine = _make_engine()
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session()

        org = Organization(name="SignOrg", code="SIGN")
        self.db.add(org)
        self.db.flush()

        self.editor = User(
            username="editor1", email="editor1@t.com",
            hashed_password=hash_password("pw"), role="user",
            status=UserStatus.active.value, organization_id=org.id,
        )
        self.admin = User(
            username="admin1", email="admin1@t.com",
            hashed_password=hash_password("pw"), role="user",
            status=UserStatus.active.value, organization_id=org.id,
        )
        self.db.add_all([self.editor, self.admin])
        self.db.flush()
        self.db.add_all([
            OrganizationMember(organization_id=org.id, user_id=self.editor.id, legal_role="editor"),
            OrganizationMember(organization_id=org.id, user_id=self.admin.id, legal_role="admin"),
        ])
        self.db.flush()

        self.org_id = org.id
        self.contract = LegalContract(
            organization_id=org.id, title="SignContract", contract_no="SC-001",
            status="active", created_by=self.editor.id,
        )
        self.db.add(self.contract)
        self.db.commit()

        def _override_db():
            yield self.db

        app.dependency_overrides[get_db] = _override_db
        self.client = TestClient(app, raise_server_exceptions=False)
        self.provider_patch = patch(
            "app.services.signing_provider_service.signing_provider_service.create_and_send",
            return_value=SigningDispatch(provider_request_id="SANDBOX-REQUEST-1"),
        )
        self.provider_patch.start()
        self.editor_headers = {"Authorization": f"Bearer {create_access_token({'sub': str(self.editor.id)})}"}
        self.admin_headers = {"Authorization": f"Bearer {create_access_token({'sub': str(self.admin.id)})}"}

    def tearDown(self):
        self.provider_patch.stop()
        app.dependency_overrides.clear()
        self.db.close()

    def _add_version(self, parse_status: str = "ready") -> LegalContractVersion:
        ver = LegalContractVersion(
            contract_id=self.contract.id, organization_id=self.org_id,
            version_no=1, parse_status=parse_status, created_by=self.editor.id,
        )
        self.db.add(ver)
        self.db.commit()
        self.db.refresh(ver)
        return ver

    def _add_sign_request(self, version_id: int, status: str = "draft") -> LegalSignRequest:
        req = LegalSignRequest(
            contract_id=self.contract.id, contract_version_id=version_id,
            organization_id=self.org_id, provider="fadada", status=status,
            initiated_by=self.editor.id,
        )
        self.db.add(req)
        self.db.commit()
        self.db.refresh(req)
        return req

    def test_create_sign_request_rejects_terminated_contract(self):
        ver = self._add_version()
        self.contract.status = "terminated"
        self.db.commit()
        resp = self.client.post(
            f"/api/legal/contracts/{self.contract.id}/sign-requests",
            json={"contract_version_id": ver.id, "provider": "fadada",
                  "parties": [{"name": "张三", "sign_order": 1}]},
            headers=self.editor_headers,
        )
        self.assertEqual(resp.status_code, 400)

    def test_create_sign_request_rejects_non_ready_version(self):
        ver = self._add_version(parse_status="needs_confirmation")
        resp = self.client.post(
            f"/api/legal/contracts/{self.contract.id}/sign-requests",
            json={"contract_version_id": ver.id, "provider": "fadada",
                  "parties": [{"name": "张三", "sign_order": 1}]},
            headers=self.editor_headers,
        )
        self.assertEqual(resp.status_code, 400)

    def test_create_sign_request_rejects_party_without_name(self):
        ver = self._add_version()
        resp = self.client.post(
            f"/api/legal/contracts/{self.contract.id}/sign-requests",
            json={"contract_version_id": ver.id, "provider": "fadada",
                  "parties": [{"name": "  ", "sign_order": 1}]},
            headers=self.editor_headers,
        )
        self.assertEqual(resp.status_code, 400)

    def test_create_sign_request_succeeds_with_ready_version(self):
        ver = self._add_version()
        resp = self.client.post(
            f"/api/legal/contracts/{self.contract.id}/sign-requests",
            json={"contract_version_id": ver.id, "provider": "fadada",
                  "parties": [{"name": "张三", "sign_order": 1}]},
            headers=self.editor_headers,
        )
        self.assertEqual(resp.status_code, 200)

    def test_send_without_approval_chain_blocked_for_editor(self):
        ver = self._add_version()
        req = self._add_sign_request(ver.id)
        resp = self.client.post(
            f"/api/legal/sign-requests/{req.id}/send",
            json={"provider_request_id": "PSN-001"},
            headers=self.editor_headers,
        )
        self.assertEqual(resp.status_code, 403)

    def test_send_without_approval_chain_allowed_for_admin(self):
        ver = self._add_version()
        req = self._add_sign_request(ver.id)
        resp = self.client.post(
            f"/api/legal/sign-requests/{req.id}/send",
            json={"provider_request_id": "PSN-002"},
            headers=self.admin_headers,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["status"], "sent")

    def test_send_with_approved_chain_allowed_for_editor(self):
        ver = self._add_version()
        req = self._add_sign_request(ver.id)
        chain = LegalApprovalChain(
            organization_id=self.org_id, target_type="sign_request", target_id=req.id,
            chain_type="serial", status="approved", created_by=self.admin.id,
        )
        self.db.add(chain)
        self.db.commit()
        resp = self.client.post(
            f"/api/legal/sign-requests/{req.id}/send",
            json={"provider_request_id": "PSN-003"},
            headers=self.editor_headers,
        )
        self.assertEqual(resp.status_code, 200)

    def test_send_blocked_when_case_archived(self):
        case = LegalCase(title="ArchivedCase", case_type="other",
                          organization_id=self.org_id, user_id=self.editor.id, status="archived")
        self.db.add(case)
        self.db.flush()
        self.contract.case_id = case.id
        self.db.commit()

        ver = self._add_version()
        req = self._add_sign_request(ver.id)
        resp = self.client.post(
            f"/api/legal/sign-requests/{req.id}/send",
            json={"provider_request_id": "PSN-004"},
            headers=self.admin_headers,
        )
        self.assertEqual(resp.status_code, 400)


class SignCallbackHardeningTests(unittest.TestCase):
    """sign_callback：签名验证、乱序/未来时间戳/失败终态检测"""

    def setUp(self):
        self.engine = _make_engine()
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session()

        org = Organization(name="CallbackOrg", code="CBO")
        self.db.add(org)
        self.db.flush()

        self.user = User(
            username="cbuser", email="cbuser@t.com",
            hashed_password=hash_password("pw"), role="user",
            status=UserStatus.active.value, organization_id=org.id,
        )
        self.db.add(self.user)
        self.db.flush()
        self.db.add(OrganizationMember(organization_id=org.id, user_id=self.user.id, legal_role="admin"))
        self.db.flush()

        self.org_id = org.id
        self.contract = LegalContract(
            organization_id=org.id, title="CBContract", contract_no="CB-001",
            status="active", created_by=self.user.id,
        )
        self.db.add(self.contract)
        self.db.flush()

        self.version = LegalContractVersion(
            contract_id=self.contract.id, organization_id=org.id,
            version_no=1, parse_status="ready", created_by=self.user.id,
        )
        self.db.add(self.version)
        self.db.flush()

        self.sign_request = LegalSignRequest(
            contract_id=self.contract.id, contract_version_id=self.version.id,
            organization_id=org.id, provider="fadada", status="sent",
            provider_request_id="PROV-REQ-1", initiated_by=self.user.id,
        )
        self.db.add(self.sign_request)
        self.db.commit()

        def _override_db():
            yield self.db

        app.dependency_overrides[get_db] = _override_db
        self.client = TestClient(app, raise_server_exceptions=False)

        self.settings_patch = patch("app.api.legal_contract_api.get_settings")
        mock_get_settings = self.settings_patch.start()
        mock_settings = MagicMock()
        mock_settings.SIGNING_WEBHOOK_SECRETS_JSON = json.dumps({"fadada": WEBHOOK_SECRET})
        mock_get_settings.return_value = mock_settings

    def tearDown(self):
        self.settings_patch.stop()
        app.dependency_overrides.clear()
        self.db.close()

    def _sign(self, payload: bytes) -> str:
        return hmac.new(WEBHOOK_SECRET.encode(), payload, hashlib.sha256).hexdigest()

    def _post_callback(self, body: dict):
        payload = json.dumps(body).encode()
        sig = self._sign(payload)
        return self.client.post(
            "/api/legal/signing/webhooks/fadada",
            content=payload,
            headers={"Content-Type": "application/json", "X-Signature": sig},
        )

    def test_invalid_signature_rejected(self):
        payload = json.dumps({
            "event_type": "signed", "provider_event_id": "EVT-1",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "provider_request_id": "PROV-REQ-1", "result": "success",
        }).encode()
        resp = self.client.post(
            "/api/legal/signing/webhooks/fadada",
            content=payload,
            headers={"Content-Type": "application/json", "X-Signature": "bad-signature"},
        )
        self.assertEqual(resp.status_code, 401)

    def test_normal_signed_event_transitions_to_signed(self):
        resp = self._post_callback({
            "event_type": "signed", "provider_event_id": "EVT-NORMAL-1",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "provider_request_id": "PROV-REQ-1", "result": "success",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["data"]["anomalous"])
        self.db.refresh(self.sign_request)
        self.assertEqual(self.sign_request.status, "signed")
        self.db.refresh(self.contract)
        self.assertEqual(self.contract.status, "signed")

    def test_future_timestamp_flagged_anomalous(self):
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        resp = self._post_callback({
            "event_type": "signed", "provider_event_id": "EVT-FUTURE-1",
            "occurred_at": future.isoformat(),
            "provider_request_id": "PROV-REQ-1", "result": "success",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["data"]["anomalous"])
        self.db.refresh(self.sign_request)
        self.assertEqual(self.sign_request.status, "needs_attention")

    def test_failed_result_on_terminal_event_flagged_anomalous(self):
        resp = self._post_callback({
            "event_type": "signed", "provider_event_id": "EVT-FAILED-1",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "provider_request_id": "PROV-REQ-1", "result": "failed",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["data"]["anomalous"])
        self.db.refresh(self.sign_request)
        self.assertEqual(self.sign_request.status, "needs_attention")
        self.assertNotEqual(self.sign_request.status, "signed")

    def test_out_of_order_event_flagged_anomalous(self):
        now = datetime.now(timezone.utc)
        first = self._post_callback({
            "event_type": "viewed", "provider_event_id": "EVT-ORDER-1",
            "occurred_at": now.isoformat(),
            "provider_request_id": "PROV-REQ-1", "result": "success",
        })
        self.assertEqual(first.status_code, 200)

        earlier = now - timedelta(minutes=10)
        second = self._post_callback({
            "event_type": "signed", "provider_event_id": "EVT-ORDER-2",
            "occurred_at": earlier.isoformat(),
            "provider_request_id": "PROV-REQ-1", "result": "success",
        })
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["data"]["anomalous"])
        self.db.refresh(self.sign_request)
        self.assertEqual(self.sign_request.status, "needs_attention")

    def test_duplicate_event_id_is_idempotent(self):
        body = {
            "event_type": "signed", "provider_event_id": "EVT-DUP-1",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "provider_request_id": "PROV-REQ-1", "result": "success",
        }
        first = self._post_callback(body)
        self.assertEqual(first.status_code, 200)
        second = self._post_callback(body)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["data"].get("idempotent"))

    def test_terminal_state_cannot_be_reversed(self):
        self._post_callback({
            "event_type": "rejected", "provider_event_id": "EVT-TERM-1",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "provider_request_id": "PROV-REQ-1", "result": "success",
        })
        self.db.refresh(self.sign_request)
        self.assertEqual(self.sign_request.status, "rejected")

        later = datetime.now(timezone.utc) + timedelta(seconds=1)
        self._post_callback({
            "event_type": "signed", "provider_event_id": "EVT-TERM-2",
            "occurred_at": later.isoformat(),
            "provider_request_id": "PROV-REQ-1", "result": "success",
        })
        self.db.refresh(self.sign_request)
        self.assertEqual(self.sign_request.status, "rejected")


class SignEvidenceTests(unittest.TestCase):
    """GET /sign-requests/{id}/evidence"""

    def setUp(self):
        self.engine = _make_engine()
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session()

        org = Organization(name="EvidOrg", code="EVID")
        self.db.add(org)
        self.db.flush()

        self.user = User(
            username="eviduser", email="eviduser@t.com",
            hashed_password=hash_password("pw"), role="user",
            status=UserStatus.active.value, organization_id=org.id,
        )
        self.outsider = User(
            username="outsider2", email="outsider2@t.com",
            hashed_password=hash_password("pw"), role="user",
            status=UserStatus.active.value,
        )
        self.db.add_all([self.user, self.outsider])
        self.db.flush()
        self.db.add(OrganizationMember(organization_id=org.id, user_id=self.user.id, legal_role="admin"))
        self.db.flush()

        self.org_id = org.id
        contract = LegalContract(
            organization_id=org.id, title="EvidContract", contract_no="EV-001",
            status="active", created_by=self.user.id,
        )
        self.db.add(contract)
        self.db.flush()
        version = LegalContractVersion(
            contract_id=contract.id, organization_id=org.id,
            version_no=1, parse_status="ready", created_by=self.user.id,
        )
        self.db.add(version)
        self.db.flush()
        self.sign_request = LegalSignRequest(
            contract_id=contract.id, contract_version_id=version.id,
            organization_id=org.id, provider="fadada", status="signed",
            provider_request_id="PROV-EV-1", initiated_by=self.user.id,
        )
        self.db.add(self.sign_request)
        self.db.commit()

        def _override_db():
            yield self.db

        app.dependency_overrides[get_db] = _override_db
        self.client = TestClient(app, raise_server_exceptions=False)
        self.headers = {"Authorization": f"Bearer {create_access_token({'sub': str(self.user.id)})}"}
        self.outsider_headers = {"Authorization": f"Bearer {create_access_token({'sub': str(self.outsider.id)})}"}

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def test_org_member_can_view_evidence(self):
        resp = self.client.get(
            f"/api/legal/sign-requests/{self.sign_request.id}/evidence",
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["status"], "signed")

    def test_outsider_cannot_view_evidence(self):
        resp = self.client.get(
            f"/api/legal/sign-requests/{self.sign_request.id}/evidence",
            headers=self.outsider_headers,
        )
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
