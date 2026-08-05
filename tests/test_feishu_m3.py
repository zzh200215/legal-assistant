"""#87/飞书 M3：审核队列（S4）+ 文书生成（S3）+ 卡片交互测试"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.services import feishu_service


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


def _bound_user(db, username="m3"):
    from app.core.auth import hash_password
    from app.models.user import User, UserStatus

    user = User(username=username, email=f"{username}@t.com", hashed_password=hash_password("pw"),
                role="user", status=UserStatus.active.value)
    db.add(user)
    db.commit()
    binding = feishu_service.FeishuBinding(user_id=user.id, open_id="ou_1", app_id="cli_app")
    db.add(binding)
    db.commit()
    return user


def _card_action_payload(open_id="ou_1", value=None):
    return {
        "header": {"event_type": "card.action.trigger", "event_id": "e3"},
        "event": {
            "operator": {"operator_id": {"open_id": open_id}},
            "action": {"tag": "button", "value": value or {"kind": "review"}},
        },
    }


class ExtractCardActionTests(unittest.TestCase):
    def test_extracts_button_action(self):
        value = {"kind": "review", "action": "approve", "target_type": "consultation", "target_id": 1}
        result = feishu_service.extract_card_action(_card_action_payload(value=value))
        self.assertEqual(result["open_id"], "ou_1")
        self.assertEqual(result["value"]["action"], "approve")

    def test_ignores_non_button_or_missing_kind(self):
        payload = _card_action_payload()
        payload["event"]["action"]["tag"] = "select"
        self.assertIsNone(feishu_service.extract_card_action(payload))
        no_kind = _card_action_payload(value={"foo": 1})
        self.assertIsNone(feishu_service.extract_card_action(no_kind))

    def test_handle_event_spawns_card_action(self):
        with patch.object(feishu_service, "_spawn_card_action") as mock_spawn:
            result = feishu_service.handle_event(_card_action_payload(value={"kind": "review"}))
        self.assertTrue(result["received"])
        mock_spawn.assert_called_once()


class DetectAndParseTests(unittest.TestCase):
    def test_detect_draft_type(self):
        self.assertEqual(feishu_service.detect_draft_type("帮我写个劳动仲裁申请书"), "labor_arbitration_application")
        self.assertEqual(feishu_service.detect_draft_type("民间借贷起诉状"), "private_lending_complaint")
        self.assertIsNone(feishu_service.detect_draft_type("你好"))

    def test_parse_draft_fields_ignores_unrelated_keys(self):
        message = "申请人:张三 被申请人:某公司 仲裁请求:支付工资 额外信息:x"
        fields = feishu_service.parse_draft_fields(message, "labor_arbitration_application")
        self.assertEqual(fields.get("申请人"), "张三")
        self.assertEqual(fields.get("被申请人"), "某公司")
        self.assertNotIn("额外信息", fields)


class ReviewQueueCardTests(unittest.TestCase):
    def test_build_review_item_card_has_buttons_and_preview(self):
        item = {
            "target_type": "consultation", "id": 7, "question": "工伤赔偿怎么算？",
            "advice": "依据条例申请工伤认定", "status": "needs_lawyer_review",
        }
        card = feishu_service.build_review_item_card(item)
        serialized = json.dumps(card, ensure_ascii=False)
        self.assertEqual(card["header"]["title"]["content"], "待审核 · 咨询")
        self.assertIn("工伤赔偿怎么算", serialized)
        self.assertIn("通过", serialized)
        self.assertIn("退回", serialized)
        actions = card["elements"][3]["actions"]
        approve_value = actions[0]["value"]
        self.assertEqual(approve_value["action"], "approve")
        self.assertEqual(approve_value["target_type"], "consultation")
        self.assertEqual(approve_value["target_id"], 7)


class AnswerReviewQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_unbound_user_gets_bind_prompt(self):
        engine, db = _make_session()
        try:
            messenger = AsyncMock()
            messenger.send_text.return_value = {"configured": False}
            with patch.object(feishu_service, "FeishuMessenger", return_value=messenger):
                await feishu_service.answer_review_queue("ou_nobody", db)
            messenger.send_text.assert_awaited_once()
            self.assertIn("绑定", messenger.send_text.call_args.args[1])
        finally:
            db.close()
            engine.dispose()

    async def test_empty_queue_sends_text(self):
        engine, db = _make_session()
        try:
            _bound_user(db)
            from app.services.legal_workspace_service import legal_workspace_read_module

            messenger = AsyncMock()
            messenger.send_text.return_value = {"configured": True, "sent": True}
            with patch.object(feishu_service, "FeishuMessenger", return_value=messenger), \
                 patch.object(legal_workspace_read_module, "review_queue", return_value=[]):
                await feishu_service.answer_review_queue("ou_1", db)
            messenger.send_text.assert_awaited_once()
            self.assertIn("没有待审核", messenger.send_text.call_args.args[1])
        finally:
            db.close()
            engine.dispose()

    async def test_queue_sends_cards_per_item(self):
        engine, db = _make_session()
        try:
            _bound_user(db)
            from app.services.legal_workspace_service import legal_workspace_read_module

            messenger = AsyncMock()
            messenger.send_card.return_value = {"configured": True, "sent": True}
            items = [
                {"target_type": "consultation", "id": 1, "question": "q1", "status": "needs_lawyer_review"},
                {"target_type": "contract_review", "id": 2, "title": "t2", "status": "pending_review"},
            ]
            with patch.object(feishu_service, "FeishuMessenger", return_value=messenger), \
                 patch.object(legal_workspace_read_module, "review_queue", return_value=items):
                result = await feishu_service.answer_review_queue("ou_1", db)
            self.assertEqual(result["cards_sent"], 2)
            self.assertEqual(messenger.send_card.await_count, 2)
        finally:
            db.close()
            engine.dispose()


class HandleReviewActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_approve_writes_back_and_confirms(self):
        engine, db = _make_session()
        try:
            _bound_user(db)
            from app.services.legal_workspace_service import legal_workspace_read_module

            messenger = AsyncMock()
            messenger.send_text.return_value = {"configured": True, "sent": True}
            with patch.object(feishu_service, "FeishuMessenger", return_value=messenger), \
                 patch.object(legal_workspace_read_module, "apply_review_action",
                              return_value={"status": "lawyer_approved"}) as mock_apply:
                await feishu_service.handle_card_action("ou_1", {
                    "kind": "review", "action": "approve", "target_type": "consultation",
                    "target_id": 7, "title": "工伤咨询",
                }, db)
            messenger.send_text.assert_awaited_once()
            self.assertIn("已通过", messenger.send_text.call_args.args[1])
            mock_apply.assert_called_once()
        finally:
            db.close()
            engine.dispose()

    async def test_reject_failure_reports_error(self):
        engine, db = _make_session()
        try:
            _bound_user(db)
            from app.services.legal_workspace_service import legal_workspace_read_module

            messenger = AsyncMock()
            messenger.send_text.return_value = {"configured": True, "sent": True}
            with patch.object(feishu_service, "FeishuMessenger", return_value=messenger), \
                 patch.object(legal_workspace_read_module, "apply_review_action",
                              side_effect=PermissionError("LEGAL_REVIEW_FORBIDDEN")):
                await feishu_service.handle_card_action("ou_1", {
                    "kind": "review", "action": "return", "target_type": "draft", "target_id": 3,
                }, db)
            self.assertIn("失败", messenger.send_text.call_args.args[1])
        finally:
            db.close()
            engine.dispose()


class DraftCardTests(unittest.TestCase):
    def test_build_draft_card(self):
        row = SimpleNamespace(content="本申请书由……", missing_fields=["证据清单"])
        card = feishu_service.build_draft_card(row, "labor_arbitration_application", ["证据清单"])
        serialized = json.dumps(card, ensure_ascii=False)
        self.assertEqual(card["header"]["title"]["content"], "文书草稿 · 劳动人事争议仲裁申请书")
        self.assertIn("证据清单", serialized)
        self.assertIn("导出 DOCX", serialized)


class AnswerDraftRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_generates_draft_card(self):
        engine, db = _make_session()
        try:
            _bound_user(db)
            from app.services.legal_workspace_service import legal_workspace_module

            messenger = AsyncMock()
            messenger.send_card.return_value = {"configured": True, "sent": True}
            row = SimpleNamespace(content="草稿正文", missing_fields=["证据清单"])
            with patch.object(feishu_service, "FeishuMessenger", return_value=messenger), \
                 patch.object(legal_workspace_module, "create_draft",
                              new=AsyncMock(return_value=(row, ["证据清单"]))):
                result = await feishu_service.answer_draft_request("ou_1", "文书 劳动仲裁申请书 申请人:张三", db)
            self.assertTrue(result["sent"])
            messenger.send_card.assert_awaited_once()
        finally:
            db.close()
            engine.dispose()

    async def test_unrecognized_type_prompts(self):
        engine, db = _make_session()
        try:
            _bound_user(db)
            messenger = AsyncMock()
            messenger.send_text.return_value = {"configured": True, "sent": True}
            with patch.object(feishu_service, "FeishuMessenger", return_value=messenger):
                await feishu_service.answer_draft_request("ou_1", "你好", db)
            self.assertIn("未识别文书类型", messenger.send_text.call_args.args[1])
        finally:
            db.close()
            engine.dispose()


class TextRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_routes_commands(self):
        with patch.object(feishu_service, "answer_review_queue", new=AsyncMock(return_value={"r": "queue"})) as mq, \
             patch.object(feishu_service, "answer_draft_request", new=AsyncMock(return_value={"r": "draft"})) as md, \
             patch.object(feishu_service, "answer_consultation", new=AsyncMock(return_value={"r": "consult"})) as mc:
            await feishu_service.answer_text_message("ou_1", "查看待审核队列", None)
            self.assertEqual(mq.call_args.args[0], "ou_1")
            await feishu_service.answer_text_message("ou_1", "帮我写个劳动仲裁申请书", None)
            self.assertEqual(md.call_args.args[1], "帮我写个劳动仲裁申请书")
            await feishu_service.answer_text_message("ou_1", "工伤赔偿标准是什么", None)
            self.assertEqual(mc.call_args.args[1], "工伤赔偿标准是什么")


if __name__ == "__main__":
    unittest.main()
