"""#87/飞书 M2：合同初筛 + 文件解析测试"""
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.services.integration import feishu_service


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


def _file_event(open_id="ou_1", file_key="file_v1_x", file_name="劳务合同.pdf"):
    return {
        "header": {"event_type": "im.message.receive_v1", "event_id": "e2"},
        "event": {
            "sender": {"sender_id": {"open_id": open_id, "union_id": "uu1"}},
            "message": {
                "message_id": "om_f1",
                "chat_id": "oc_1",
                "message_type": "file",
                "content": json.dumps({"file_key": file_key, "file_name": file_name}),
            },
        },
    }


class ExtractFileEventTests(unittest.TestCase):
    def test_extracts_file_message(self):
        result = feishu_service.extract_file_event(_file_event())
        self.assertEqual(result["open_id"], "ou_1")
        self.assertEqual(result["file_key"], "file_v1_x")
        self.assertEqual(result["file_name"], "劳务合同.pdf")
        self.assertEqual(result["message_id"], "om_f1")

    def test_ignores_non_file(self):
        text_payload = {
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_id": {"open_id": "ou_1"}},
                "message": {"message_type": "text", "content": json.dumps({"text": "hi"})},
            },
        }
        self.assertIsNone(feishu_service.extract_file_event(text_payload))

    def test_ignores_missing_file_key_or_open_id(self):
        no_key = _file_event()
        no_key["event"]["message"]["content"] = json.dumps({"file_name": "x.pdf"})
        self.assertIsNone(feishu_service.extract_file_event(no_key))
        self.assertIsNone(feishu_service.extract_file_event(_file_event(open_id=None)))


class HandleEventFileTests(unittest.TestCase):
    def test_file_event_spawns_background_review(self):
        with patch.object(feishu_service, "_spawn_file_review") as mock_spawn:
            result = feishu_service.handle_event(_file_event())
        self.assertTrue(result["received"])
        mock_spawn.assert_called_once()
        self.assertEqual(mock_spawn.call_args.args[0]["file_key"], "file_v1_x")


class BuildContractReviewCardTests(unittest.TestCase):
    def test_card_shows_risks_and_summary(self):
        risks = [
            {"clause_type": "termination", "label": "单方解除权", "risk_level": "high",
             "source_location": {"paragraph": 3, "snippet": "甲方有权随时单方解除本协议"},
             "suggestion": "约定提前通知期限"},
            {"clause_type": "liability", "label": "免责条款", "risk_level": "medium",
             "source_location": {"paragraph": 5, "snippet": "因不可抗力免责"},
             "suggestion": "补充不可抗力证明要求"},
        ]
        card = feishu_service.build_contract_review_card(risks, "共识别 2 项审查提示，其中高风险 1 项", "劳务合同.pdf")
        serialized = json.dumps(card, ensure_ascii=False)
        self.assertEqual(card["header"]["template"], "red")  # 含 high
        self.assertIn("共识别 2 项审查提示", serialized)
        self.assertIn("单方解除权", serialized)
        self.assertIn("约定提前通知期限", serialized)
        self.assertIn("深度审查", serialized)
        self.assertIn("仅供参考", serialized)

    def test_card_empty_risks_uses_orange_header(self):
        card = feishu_service.build_contract_review_card([], "未识别到风险条款", "a.pdf")
        self.assertEqual(card["header"]["template"], "orange")
        self.assertIn("未识别到明确风险条款", json.dumps(card, ensure_ascii=False))


class ExtractFileTextTests(unittest.TestCase):
    def test_extracts_plain_text_file(self):
        result = feishu_service._extract_file_text("这是合同正文内容。".encode("utf-8"), "合同.txt")
        self.assertIn("这是合同正文内容", result)


class AnswerFileReviewTests(unittest.IsolatedAsyncioTestCase):
    def _make_bound(self):
        engine, db = _make_session()
        from app.core.auth import hash_password
        from app.models.user import User, UserStatus

        user = User(username="m2", email="m2@t.com", hashed_password=hash_password("pw"),
                    role="user", status=UserStatus.active.value)
        db.add(user)
        db.commit()
        binding = feishu_service.FeishuBinding(user_id=user.id, open_id="ou_1", app_id="cli_app")
        db.add(binding)
        db.commit()
        return engine, db

    async def test_unbound_user_gets_bind_prompt(self):
        engine, db = _make_session()
        try:
            messenger = AsyncMock()
            messenger.send_text.return_value = {"configured": False}
            with patch.object(feishu_service, "FeishuMessenger", return_value=messenger):
                await feishu_service.answer_file_review("ou_nobody", "fk", "a.pdf", db)
            messenger.send_text.assert_awaited_once()
            self.assertIn("绑定", messenger.send_text.call_args.args[1])
        finally:
            db.close()
            engine.dispose()

    async def test_download_disabled_returns_placeholder_message(self):
        engine, db = self._make_bound()
        try:
            messenger = AsyncMock()
            messenger.download_file.return_value = None
            messenger.send_text.return_value = {"configured": True, "sent": True}
            with patch.object(feishu_service, "FeishuMessenger", return_value=messenger):
                result = await feishu_service.answer_file_review("ou_1", "fk", "a.pdf", db)
            self.assertTrue(result["sent"])
            self.assertIn("尚未开通", messenger.send_text.call_args.args[1])
        finally:
            db.close()
            engine.dispose()

    async def test_bound_user_reviews_and_sends_card(self):
        engine, db = self._make_bound()
        try:
            messenger = AsyncMock()
            messenger.download_file.return_value = b"contract bytes"
            messenger.send_card.return_value = {"configured": True, "sent": True, "message_id": "om_new"}
            risks = [{"clause_type": "termination", "label": "单方解除权", "risk_level": "high",
                      "source_location": {"snippet": "甲方有权单方解除"}, "suggestion": "加提前通知期"}]
            with patch.object(feishu_service, "FeishuMessenger", return_value=messenger), \
                 patch.object(feishu_service, "_extract_file_text", return_value="合同正文，甲方有权单方解除本协议"), \
                 patch("app.services.legal.legal_service.review_contract", new=AsyncMock(
                     return_value=(risks, "共识别 1 项审查提示"))):
                result = await feishu_service.answer_file_review("ou_1", "fk", "劳务合同.pdf", db)
            self.assertTrue(result["sent"])
            messenger.send_card.assert_awaited_once()
            card = messenger.send_card.call_args.args[1]
            self.assertEqual(card["header"]["title"]["content"], "合同初筛 · 劳务合同.pdf")
            self.assertIn("单方解除权", json.dumps(card, ensure_ascii=False))
        finally:
            db.close()
            engine.dispose()


class MessengerDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_download_not_configured_returns_none(self):
        messenger = feishu_service.FeishuMessenger("", "")
        self.assertIsNone(await messenger.download_file("fk"))

    async def test_download_with_mocked_http(self):
        messenger = feishu_service.FeishuMessenger("cli_app", "secret")
        fake_client = AsyncMock()
        token_resp = MagicMock()
        token_resp.json.return_value = {"code": 0, "tenant_access_token": "t-123", "expire": 7200}
        file_resp = MagicMock()
        file_resp.headers = {"content-type": "application/pdf"}
        file_resp.content = b"%PDF-bytes"
        fake_client.post.return_value = token_resp
        fake_client.get.return_value = file_resp
        with patch.object(messenger, "_client", new=AsyncMock(return_value=fake_client)):
            data = await messenger.download_file("fk")
        self.assertEqual(data, b"%PDF-bytes")
        self.assertIn("/im/v1/files/fk", fake_client.get.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
