"""P1-C Webhook 统一验证与防重放：签名正确/错误/缺失、过期、重放、并发重放、
缺少字段、载荷篡改、fail-closed、审计不含密钥/完整载荷。"""

import base64
import hashlib
import hmac
import json
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import hash_password
from app.core.config import get_settings
from app.core.database import Base, get_db
from app.core.webhook_dedup import claim_nonce
from app.core.webhook_verifier import WebhookVerificationError, WebhookVerifier
from app.main import app
from app.models.legal_notifications import SecurityAuditEvent
from app.models.user import User

_SECRET = "test-webhook-secret-0123456789"


def _raw_hex_sig(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _stripe_sig(secret: str, body: bytes, ts: int | None = None, *, sig: str | None = None) -> str:
    ts = ts if ts is not None else int(time.time())
    expected = hmac.new(secret.encode(), f"{ts}.{body.decode('utf-8')}".encode(), hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig or expected}"


def _feishu_v2_sig(secret: str, body: bytes, ts: str, nonce: str) -> str:
    v2_input = f"{ts}{nonce}{secret}".encode("utf-8") + body
    return base64.b64encode(hmac.new(secret.encode(), v2_input, hashlib.sha256).digest()).decode("ascii")


def _feishu_v1_sig(secret: str, body: bytes) -> str:
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode("ascii")


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )


class WebhookVerifierTests(unittest.TestCase):
    def setUp(self):
        self.body = json.dumps({"event_type": "signed", "provider_event_id": "EVT-1"}).encode("utf-8")

    # ── raw scheme（签署回调） ─────────────────────────────

    def test_raw_valid_signature_passes(self):
        WebhookVerifier(_SECRET, scheme="raw", encoding="hex").verify(
            self.body, _raw_hex_sig(_SECRET, self.body))

    def test_raw_wrong_signature_rejected(self):
        with self.assertRaises(WebhookVerificationError) as ctx:
            WebhookVerifier(_SECRET, scheme="raw", encoding="hex").verify(
                self.body, _raw_hex_sig("other-secret", self.body))
        self.assertEqual(ctx.exception.code, "INVALID_SIGNATURE")

    def test_raw_missing_signature_rejected(self):
        with self.assertRaises(WebhookVerificationError) as ctx:
            WebhookVerifier(_SECRET, scheme="raw", encoding="hex").verify(self.body, None)
        self.assertEqual(ctx.exception.code, "MISSING_FIELD")

    def test_tampered_payload_rejected(self):
        # 签名针对原请求体；换内容即失效（防篡改）
        tampered = json.dumps({"event_type": "signed", "provider_event_id": "EVT-2"}).encode("utf-8")
        with self.assertRaises(WebhookVerificationError) as ctx:
            WebhookVerifier(_SECRET, scheme="raw", encoding="hex").verify(
                tampered, _raw_hex_sig(_SECRET, self.body))
        self.assertEqual(ctx.exception.code, "INVALID_SIGNATURE")

    def test_no_secret_fails_closed(self):
        with self.assertRaises(WebhookVerificationError) as ctx:
            WebhookVerifier("", scheme="raw", encoding="hex").verify(self.body, "anything")
        self.assertEqual(ctx.exception.code, "NOT_CONFIGURED")

    def test_error_message_never_contains_secret(self):
        try:
            WebhookVerifier(_SECRET, scheme="raw", encoding="hex").verify(self.body, "bad")
        except WebhookVerificationError as exc:
            self.assertNotIn(_SECRET, str(exc))

    # ── stripe scheme ──────────────────────────────────────

    def test_stripe_valid_signature_passes(self):
        WebhookVerifier(_SECRET, scheme="stripe").verify(self.body, _stripe_sig(_SECRET, self.body))

    def test_stripe_expired_signature_rejected(self):
        sig = _stripe_sig(_SECRET, self.body, ts=int(time.time()) - 3600)
        with self.assertRaises(WebhookVerificationError) as ctx:
            WebhookVerifier(_SECRET, scheme="stripe", tolerance_seconds=300).verify(self.body, sig)
        self.assertEqual(ctx.exception.code, "EXPIRED")

    def test_stripe_missing_header_fields_rejected(self):
        with self.assertRaises(WebhookVerificationError) as ctx:
            WebhookVerifier(_SECRET, scheme="stripe").verify(self.body, "v1=only-no-ts")
        self.assertEqual(ctx.exception.code, "MISSING_FIELD")

    def test_stripe_tampered_payload_rejected(self):
        tampered = b'{"id":"evt_x","created":123,"data":{}}'
        sig = _stripe_sig(_SECRET, self.body)
        with self.assertRaises(WebhookVerificationError) as ctx:
            WebhookVerifier(_SECRET, scheme="stripe").verify(tampered, sig)
        self.assertEqual(ctx.exception.code, "INVALID_SIGNATURE")

    # ── feishu scheme ──────────────────────────────────────

    def test_feishu_v2_valid_passes(self):
        ts, nonce = str(int(time.time())), "n1"
        WebhookVerifier(_SECRET, scheme="feishu_v2").verify(
            self.body, _feishu_v2_sig(_SECRET, self.body, ts, nonce), timestamp=ts, nonce=nonce)

    def test_feishu_v2_stale_timestamp_rejected(self):
        ts, nonce = str(int(time.time()) - 3600), "n2"
        with self.assertRaises(WebhookVerificationError) as ctx:
            WebhookVerifier(_SECRET, scheme="feishu_v2").verify(
                self.body, _feishu_v2_sig(_SECRET, self.body, ts, nonce), timestamp=ts, nonce=nonce)
        self.assertEqual(ctx.exception.code, "EXPIRED")

    def test_feishu_v2_missing_nonce_rejected(self):
        ts = str(int(time.time()))
        with self.assertRaises(WebhookVerificationError) as ctx:
            WebhookVerifier(_SECRET, scheme="feishu_v2").verify(
                self.body, "whatever", timestamp=ts, nonce=None)
        self.assertEqual(ctx.exception.code, "MISSING_FIELD")

    def test_feishu_v1_valid_passes(self):
        WebhookVerifier(_SECRET, scheme="feishu_v1").verify(self.body, _feishu_v1_sig(_SECRET, self.body))

    def test_feishu_auto_accepts_legacy_hex(self):
        WebhookVerifier(_SECRET, scheme="feishu_auto").verify(
            self.body, _raw_hex_sig(_SECRET, self.body))

    def test_feishu_auto_rejects_unknown_signature(self):
        with self.assertRaises(WebhookVerificationError) as ctx:
            WebhookVerifier(_SECRET, scheme="feishu_auto").verify(self.body, "totally-bogus")
        self.assertEqual(ctx.exception.code, "INVALID_SIGNATURE")


class NonceDedupTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)

    def tearDown(self):
        self.engine.dispose()

    def test_replay_rejected(self):
        db = self.Session()
        try:
            self.assertTrue(claim_nonce(db, namespace="feishu", nonce="n1", ttl_seconds=3600))
            self.assertFalse(claim_nonce(db, namespace="feishu", nonce="n1", ttl_seconds=3600))
            # 不同命名空间互不影响
            self.assertTrue(claim_nonce(db, namespace="stripe", nonce="n1", ttl_seconds=3600))
        finally:
            db.close()

    def test_empty_nonce_not_deduped(self):
        db = self.Session()
        try:
            self.assertTrue(claim_nonce(db, namespace="feishu", nonce=None, ttl_seconds=3600))
            self.assertTrue(claim_nonce(db, namespace="feishu", nonce="", ttl_seconds=3600))
        finally:
            db.close()

    def test_expired_nonce_reusable(self):
        db = self.Session()
        try:
            self.assertTrue(claim_nonce(db, namespace="feishu", nonce="old", ttl_seconds=1))
            self.assertFalse(claim_nonce(db, namespace="feishu", nonce="old", ttl_seconds=1))
            self.assertTrue(claim_nonce(db, namespace="feishu", nonce="fresh", ttl_seconds=3600))
        finally:
            db.close()

    def test_concurrent_replay_only_one_success(self):
        """并发重放：两个独立连接同时登记同一 nonce，恰好一个成功（共享存储唯一约束）。

        用文件型 SQLite（两个连接真并发），模拟多实例部署下的去重。
        """
        import tempfile
        from pathlib import Path

        tmpdir = tempfile.mkdtemp()
        db_path = str(Path(tmpdir) / "nonce.db")
        Base.metadata.create_all(create_engine(f"sqlite:///{db_path}", future=True))
        barrier = threading.Barrier(2)
        outcomes: list[bool] = []
        errors: list[Exception] = []

        def _worker():
            try:
                engine = create_engine(f"sqlite:///{db_path}", future=True)
                Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
                db = Session()
                try:
                    barrier.wait(timeout=10)
                    outcomes.append(claim_nonce(db, namespace="feishu", nonce="race", ttl_seconds=3600))
                finally:
                    db.close()
                    engine.dispose()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=_worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
        self.assertEqual(errors, [], f"并发登记不应抛异常: {errors}")
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(sorted(outcomes), [False, True])


class WebhookApiAuditTests(unittest.TestCase):
    """API 层：验签失败写安全审计，且审计不含签名密钥/完整载荷。"""

    def setUp(self):
        self.engine = _engine()
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(self.engine)
        self.db = Session()
        self.user = User(username="wh1", email="wh1@t.com", hashed_password=hash_password("pw"), role="user")
        self.db.add(self.user)
        self.db.commit()

        def _override_db():
            db = Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = _override_db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()
        self.engine.dispose()

    def test_feishu_blocked_callback_audited_without_secret_or_payload(self):
        import app.api.channels.feishu_api as feishu_api

        payload = json.dumps({"type": "message", "event": {"x": 1}}).encode("utf-8")
        with patch.object(feishu_api, "settings") as mock_settings:
            mock_settings.FEISHU_EVENT_ENCRYPT_KEY = "supersecret-key"
            mock_settings.FEISHU_CALLBACK_VERIFY = "v2"
            resp = self.client.post(
                "/api/feishu/callbacks/event",
                content=payload,
                headers={"Content-Type": "application/json", "x-lark-signature": "bad-sig"},
            )
        self.assertEqual(resp.status_code, 400, resp.text)
        events = self.db.query(SecurityAuditEvent).filter(SecurityAuditEvent.event_type == "webhook").all()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].result, "blocked")
        self.assertEqual(events[0].reason_code, "MISSING_FIELD")
        meta = events[0].sanitized_metadata or ""
        self.assertNotIn("supersecret-key", meta)
        self.assertNotIn(payload.decode("utf-8", "replace"), meta)

    def test_signing_webhook_invalid_signature_audited(self):
        payload = json.dumps({
            "event_type": "signed", "provider_event_id": "EVT-AUDIT-1",
            "occurred_at": "2026-01-01T00:00:00+00:00",
            "provider_request_id": "PROV-1", "result": "success",
        }).encode("utf-8")
        with patch("app.api.legal.legal_contract_api.get_settings") as mock_get:
            mock_settings = type("S", (), {"SIGNING_WEBHOOK_SECRETS_JSON": json.dumps({"fadada": _SECRET})})()
            mock_get.return_value = mock_settings
            resp = self.client.post(
                "/api/legal/signing/webhooks/fadada",
                content=payload,
                headers={"Content-Type": "application/json", "X-Signature": "bad-signature"},
            )
        self.assertEqual(resp.status_code, 401, resp.text)
        events = self.db.query(SecurityAuditEvent).filter(SecurityAuditEvent.event_type == "webhook").all()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].reason_code, "INVALID_SIGNATURE")
        self.assertNotIn(_SECRET, events[0].sanitized_metadata or "")


if __name__ == "__main__":
    unittest.main()