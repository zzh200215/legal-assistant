"""#87/飞书 M1：事件解密 + 单聊咨询卡片（法条核对）测试"""
import base64
import hashlib
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.services import feishu_service
from app.services.rag_service import rag_service


def _make_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return engine, Session()


def _feishu_encrypt(encrypt_key: str, plaintext: dict) -> str:
    key = hashlib.md5(encrypt_key.encode("utf-8")).hexdigest().encode("utf-8")
    iv = key[:16]
    raw = json.dumps(plaintext, ensure_ascii=False).encode("utf-8")
    padder = padding.PKCS7(128).padder()
    data = padder.update(raw) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    enc = cipher.encryptor()
    return base64.b64encode(enc.update(data) + enc.finalize()).decode("utf-8")


class DecryptTests(unittest.TestCase):
    def test_decrypt_round_trip(self):
        original = {"type": "url_verification", "challenge": "abc123"}
        encrypted = _feishu_encrypt("enc_key_test", original)
        self.assertEqual(feishu_service.decrypt_payload("enc_key_test", encrypted), original)

    def test_decrypt_missing_key_raises(self):
        with self.assertRaises(ValueError):
            feishu_service.decrypt_payload("", "AAAA")

    def test_parse_event_body_decrypts_when_encrypted(self):
        original = {"type": "message", "event": {"x": 1}}
        body = json.dumps({"encrypt": _feishu_encrypt("enc_key_test", original)}).encode("utf-8")
        self.assertEqual(feishu_service.parse_event_body(body, "enc_key_test"), original)

    def test_parse_event_body_passthrough_plaintext(self):
        body = json.dumps({"type": "message"}).encode("utf-8")
        self.assertEqual(feishu_service.parse_event_body(body, "enc_key_test"), {"type": "message"})


class ExtractEventTests(unittest.TestCase):
    def _message_event(self, message_type="text", content=None, open_id="ou_1"):
        return {
            "header": {"event_type": "im.message.receive_v1", "event_id": "e1"},
            "event": {
                "sender": {"sender_id": {"open_id": open_id, "union_id": "uu1"}},
                "message": {
                    "message_id": "om_1",
                    "chat_id": "oc_1",
                    "message_type": message_type,
                    "content": json.dumps(content) if content is not None else "",
                },
            },
        }

    def test_extracts_text_message(self):
        result = feishu_service.extract_message_event(self._message_event(content={"text": " 请问工伤赔偿标准  "}))
        self.assertEqual(result["open_id"], "ou_1")
        self.assertEqual(result["text"], "请问工伤赔偿标准")
        self.assertEqual(result["message_id"], "om_1")

    def test_ignores_non_text_or_missing(self):
        self.assertIsNone(feishu_service.extract_message_event(self._message_event(message_type="image", content={})))
        self.assertIsNone(feishu_service.extract_message_event(self._message_event(content=None)))
        self.assertIsNone(feishu_service.extract_message_event(self._message_event(open_id=None, content={"text": "hi"})))


class HandleEventTests(unittest.TestCase):
    def test_url_verification_returns_challenge(self):
        result = feishu_service.handle_event({"type": "url_verification", "challenge": "xyz"})
        self.assertEqual(result["challenge"], "xyz")

    def test_message_event_spawns_background_reply(self):
        payload = {
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_id": {"open_id": "ou_1"}},
                "message": {"message_id": "m1", "message_type": "text", "content": json.dumps({"text": "你好"})},
            },
        }
        with patch.object(feishu_service, "_spawn_reply") as mock_spawn:
            result = feishu_service.handle_event(payload)
        self.assertTrue(result["received"])
        mock_spawn.assert_called_once()
        self.assertEqual(mock_spawn.call_args.args[0]["text"], "你好")

    def test_other_event_acks(self):
        result = feishu_service.handle_event({"header": {"event_type": "app.status_change"}})
        self.assertTrue(result["received"])


class BuildCardTests(unittest.IsolatedAsyncioTestCase):
    async def test_card_built_from_consultation(self):
        engine, db = _make_session()
        try:
            with patch("app.services.legal_service.consultation_payload", new=AsyncMock(return_value=(
                    "劳动纠纷", ["已知"], [], [{"title": "工伤保险条例", "citation": "2010年修订"}],
                    "依据条例申请工伤认定", "medium", "pending_review"))), \
                 patch("app.services.legal_service.ensure_demo_sources", new=MagicMock()), \
                 patch.object(rag_service, "search_async", new=AsyncMock(return_value=[])):
                card = await feishu_service.build_consultation_card("工伤", 1, db)
            serialized = json.dumps(card, ensure_ascii=False)
            self.assertEqual(card["header"]["title"]["content"], "法条核对 · 劳动纠纷")
            self.assertIn("工伤保险条例", serialized)
            self.assertIn("不构成最终法律意见", serialized)
        finally:
            db.close()
            engine.dispose()


class AnswerConsultationTests(unittest.IsolatedAsyncioTestCase):
    def _make_bound(self):
        engine, db = _make_session()
        from app.core.auth import hash_password
        from app.models.user import User, UserStatus

        user = User(username="m1", email="m1@t.com", hashed_password=hash_password("pw"),
                    role="user", status=UserStatus.active.value)
        db.add(user)
        db.commit()
        binding = feishu_service.FeishuBinding(user_id=user.id, open_id="ou_1", app_id="cli_app")
        db.add(binding)
        db.commit()
        return engine, db, user.id

    async def test_unbound_user_gets_bind_prompt(self):
        engine, db = _make_session()
        try:
            messenger = AsyncMock()
            messenger.send_text.return_value = {"configured": False}
            with patch.object(feishu_service, "FeishuMessenger", return_value=messenger):
                await feishu_service.answer_consultation("ou_nobody", "问", db)
            messenger.send_text.assert_awaited_once()
            self.assertIn("绑定", messenger.send_text.call_args.args[1])
        finally:
            db.close()
            engine.dispose()

    async def test_bound_user_builds_card_and_sends(self):
        engine, db, _user_id = self._make_bound()
        try:
            messenger = AsyncMock()
            messenger.send_card.return_value = {"configured": True, "sent": True, "message_id": "om_new"}
            with patch.object(feishu_service, "FeishuMessenger", return_value=messenger), \
                 patch.object(feishu_service, "build_consultation_card", new=AsyncMock(return_value={"k": "v"})):
                result = await feishu_service.answer_consultation("ou_1", "工伤赔偿标准", db)
            self.assertTrue(result["sent"])
            messenger.send_card.assert_awaited_once()
            self.assertEqual(messenger.send_card.call_args.args[0], "ou_1")
        finally:
            db.close()
            engine.dispose()


class MessengerTests(unittest.IsolatedAsyncioTestCase):
    async def test_not_configured_returns_false_without_http(self):
        messenger = feishu_service.FeishuMessenger("", "")
        self.assertIsNone(await messenger.tenant_access_token())
        self.assertEqual(await messenger.send_card("ou_1", {}), {"configured": False})

    async def test_send_with_mocked_http(self):
        messenger = feishu_service.FeishuMessenger("cli_app", "secret")
        fake_client = AsyncMock()
        token_resp = MagicMock()
        token_resp.json.return_value = {"code": 0, "tenant_access_token": "t-123", "expire": 7200}
        send_resp = MagicMock()
        send_resp.json.return_value = {"code": 0, "data": {"message_id": "om_new"}}
        fake_client.post.side_effect = [token_resp, send_resp]
        with patch.object(messenger, "_client", new=AsyncMock(return_value=fake_client)):
            result = await messenger.send_card("ou_1", {"elements": []})
        self.assertTrue(result["configured"])
        self.assertTrue(result["sent"])
        self.assertEqual(result["message_id"], "om_new")
        send_body = fake_client.post.call_args_list[1].kwargs["json"]
        self.assertEqual(send_body["receive_id"], "ou_1")
        self.assertEqual(send_body["msg_type"], "interactive")
        self.assertIsInstance(send_body["content"], str)


if __name__ == "__main__":
    unittest.main()
