"""#87/飞书绑定与回调 API 回归测试"""
import base64
import hashlib
import hmac
import time
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import hash_password, create_access_token
from app.core.database import Base, get_db
from app.main import app
from app.models.user import User, UserStatus
from app.models.feishu_binding import FeishuBinding


def _make_engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


class FeishuApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session()

        self.user = User(
            username="feishu1", email="f1@t.com",
            hashed_password=hash_password("pw"), role="user",
            status=UserStatus.active.value,
        )
        self.admin = User(
            username="fadmin", email="fa@t.com",
            hashed_password=hash_password("pw"), role="admin",
            status=UserStatus.active.value,
        )
        self.db.add_all([self.user, self.admin])
        self.db.commit()

        self.token = create_access_token({"sub": str(self.user.id)})
        self.admin_token = create_access_token({"sub": str(self.admin.id)})

        def _override_db():
            db = Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = _override_db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.pop(get_db, None)
        self.db.close()
        self.engine.dispose()

    def _headers(self, token):
        return {"Authorization": f"Bearer {token}"}

    def test_bind_me(self):
        r = self.client.post(
            "/api/feishu/bindings",
            json={"open_id": "ou_abc12345", "app_id": "cli_testapp"},
            headers=self._headers(self.token),
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["data"]["binding_id"] > 0)

        r = self.client.get("/api/feishu/bindings/me", headers=self._headers(self.token))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["data"]["bound"])

    def test_bind_conflict(self):
        self.client.post(
            "/api/feishu/bindings",
            json={"open_id": "ou_abc12345", "app_id": "cli_testapp"},
            headers=self._headers(self.token),
        )
        r = self.client.post(
            "/api/feishu/bindings",
            json={"open_id": "ou_abc12345", "app_id": "cli_testapp"},
            headers=self._headers(self.admin_token),
        )
        self.assertEqual(r.status_code, 409, r.text)

    def test_unbind(self):
        self.client.post(
            "/api/feishu/bindings",
            json={"open_id": "ou_abc12345", "app_id": "cli_testapp"},
            headers=self._headers(self.token),
        )
        r = self.client.delete("/api/feishu/bindings/me", headers=self._headers(self.token))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["data"]["revoked"])

    def test_callback_url_verification(self):
        import json

        challenge = "abc123"
        r = self.client.post(
            "/api/feishu/callbacks/event",
            json={"type": "url_verification", "challenge": challenge},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["data"]["challenge"], challenge)

    def test_callback_signature_verified_when_key_configured(self):
        import json

        import app.api.channels.feishu_api as feishu_api

        payload = json.dumps({"type": "message", "event": {"x": 1}}).encode("utf-8")
        with __import__("unittest.mock").mock.patch.object(feishu_api, "settings") as mock_settings:
            mock_settings.FEISHU_EVENT_ENCRYPT_KEY = "enc_key_test"
            mock_settings.FEISHU_CALLBACK_VERIFY = "auto"
            mock_settings.ALERT_WEBHOOK_URL = ""
            # 无签名 → 400
            r = self.client.post(
                "/api/feishu/callbacks/event",
                content=payload,
                headers={"Content-Type": "application/json"},
            )
            self.assertEqual(r.status_code, 400, r.text)
            # 正确签名 → 200（auto 模式：V2/V1 都不匹配，落到兼容 hex 分支）
            sig = hmac.new(b"enc_key_test", payload, hashlib.sha256).hexdigest()
            r = self.client.post(
                "/api/feishu/callbacks/event",
                content=payload,
                headers={"Content-Type": "application/json", "x-lark-signature": sig},
            )
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(r.json()["success"])

    def test_callback_signature_v2(self):
        import json

        import app.api.channels.feishu_api as feishu_api

        payload = json.dumps({"type": "message", "event": {"x": 1}}).encode("utf-8")
        # P1-C：v2 要求时间戳在有效窗口内（WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS，默认 300s）
        ts, nonce = str(int(time.time())), "rand123"
        with __import__("unittest.mock").mock.patch.object(feishu_api, "settings") as mock_settings:
            mock_settings.FEISHU_EVENT_ENCRYPT_KEY = "enc_key_test"
            mock_settings.FEISHU_CALLBACK_VERIFY = "auto"
            mock_settings.ALERT_WEBHOOK_URL = ""
            # V2：base64(HmacSHA256(timestamp+nonce+encrypt_key+body, encrypt_key))
            v2_input = f"{ts}{nonce}enc_key_test".encode("utf-8") + payload
            sig = base64.b64encode(
                hmac.new(b"enc_key_test", v2_input, hashlib.sha256).digest()
            ).decode("utf-8")
            r = self.client.post(
                "/api/feishu/callbacks/event",
                content=payload,
                headers={
                    "Content-Type": "application/json",
                    "x-lark-signature": sig,
                    "x-lark-request-timestamp": ts,
                    "x-lark-request-nonce": nonce,
                },
            )
            self.assertEqual(r.status_code, 200, r.text)

    def test_callback_signature_v1(self):
        import json

        import app.api.channels.feishu_api as feishu_api

        payload = json.dumps({"type": "message", "event": {"x": 1}}).encode("utf-8")
        with __import__("unittest.mock").mock.patch.object(feishu_api, "settings") as mock_settings:
            mock_settings.FEISHU_EVENT_ENCRYPT_KEY = "enc_key_test"
            mock_settings.FEISHU_CALLBACK_VERIFY = "auto"
            mock_settings.ALERT_WEBHOOK_URL = ""
            # V1：base64(HmacSHA256(body, encrypt_key))
            sig = base64.b64encode(
                hmac.new(b"enc_key_test", payload, hashlib.sha256).digest()
            ).decode("utf-8")
            r = self.client.post(
                "/api/feishu/callbacks/event",
                content=payload,
                headers={"Content-Type": "application/json", "x-lark-signature": sig},
            )
            self.assertEqual(r.status_code, 200, r.text)

    def test_callback_signature_v2_only_mode(self):
        import json

        import app.api.channels.feishu_api as feishu_api

        payload = json.dumps({"type": "message", "event": {"x": 1}}).encode("utf-8")
        with __import__("unittest.mock").mock.patch.object(feishu_api, "settings") as mock_settings:
            mock_settings.FEISHU_EVENT_ENCRYPT_KEY = "enc_key_test"
            mock_settings.FEISHU_CALLBACK_VERIFY = "v2"
            mock_settings.ALERT_WEBHOOK_URL = ""
            # v2 模式：hex 与 V1 均不应通过，仅 V2 通过
            hex_sig = hmac.new(b"enc_key_test", payload, hashlib.sha256).hexdigest()
            r = self.client.post(
                "/api/feishu/callbacks/event",
                content=payload,
                headers={"Content-Type": "application/json", "x-lark-signature": hex_sig},
            )
            self.assertEqual(r.status_code, 400, r.text)

    def test_callback_signature_off_mode_skips_verification(self):
        import json

        import app.api.channels.feishu_api as feishu_api

        payload = json.dumps({"type": "url_verification", "challenge": "abc"}).encode("utf-8")
        with __import__("unittest.mock").mock.patch.object(feishu_api, "settings") as mock_settings:
            mock_settings.FEISHU_EVENT_ENCRYPT_KEY = "enc_key_test"
            mock_settings.FEISHU_CALLBACK_VERIFY = "off"
            mock_settings.ALERT_WEBHOOK_URL = ""
            # off：无签名也放行（临时排查）
            r = self.client.post(
                "/api/feishu/callbacks/event",
                content=payload,
                headers={"Content-Type": "application/json"},
            )
            self.assertEqual(r.status_code, 200, r.text)


if __name__ == "__main__":
    unittest.main()
