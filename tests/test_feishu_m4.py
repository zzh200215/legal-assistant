"""#87/飞书 M4：提醒类（激活引导 / 周报回访）测试"""
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


def _bound_user(db, username="m4", open_id="ou_1"):
    from app.core.auth import hash_password
    from app.models.user import User, UserStatus

    user = User(username=username, email=f"{username}@t.com", hashed_password=hash_password("pw"),
                role="user", status=UserStatus.active.value)
    db.add(user)
    db.commit()
    binding = feishu_service.FeishuBinding(user_id=user.id, open_id=open_id, app_id="cli_app")
    db.add(binding)
    db.commit()
    return user


def _seed_activity(db, user_id):
    from app.models.legal import LegalConsultation

    db.add(LegalConsultation(user_id=user_id, question="工伤怎么赔", category="劳动纠纷",
                             advice="依据条例申请", risk_level="low", status="pending_review"))
    db.commit()


class CardTests(unittest.TestCase):
    def test_activation_card(self):
        card = feishu_service.build_activation_card("张三")
        serialized = json.dumps(card, ensure_ascii=False)
        self.assertEqual(card["header"]["title"]["content"], "欢迎使用律智检")
        self.assertIn("法条核对卡片", serialized)
        self.assertIn("合同风险初筛", serialized)
        self.assertIn("待审核", serialized)

    def test_weekly_digest_card(self):
        stats = {"consultation_count": 3, "review_count": 2, "draft_count": 1}
        card = feishu_service.build_weekly_digest_card(stats, 5)
        serialized = json.dumps(card, ensure_ascii=False)
        self.assertIn("周报回访", serialized)
        self.assertIn("咨询 3 次", serialized)
        self.assertIn("待审核 5 项", serialized)


class ActivityStatsTests(unittest.TestCase):
    def test_counts_per_user(self):
        engine, db = _make_session()
        try:
            user_a = _bound_user(db, "a", "ou_a")
            user_b = _bound_user(db, "b", "ou_b")
            _seed_activity(db, user_a.id)
            stats_a = feishu_service.user_activity_stats(db, user_a.id)
            stats_b = feishu_service.user_activity_stats(db, user_b.id)
            self.assertEqual(stats_a["consultation_count"], 1)
            self.assertEqual(stats_b["consultation_count"], 0)
        finally:
            db.close()
            engine.dispose()


class ReminderDueTests(unittest.TestCase):
    def test_redis_failure_lets_through(self):
        import redis as redis_lib

        with patch.object(redis_lib, "from_url", side_effect=Exception("no redis")):
            self.assertTrue(feishu_service._reminder_due("ou_1", "activation"))

    def test_redis_nx_miss_means_not_due(self):
        import redis as redis_lib

        client = MagicMock()
        client.set.return_value = None  # key already exists → not due
        with patch.object(redis_lib, "from_url", return_value=client):
            self.assertFalse(feishu_service._reminder_due("ou_1", "activation"))
        client.set.assert_called_once()


class DispatchRemindersTests(unittest.IsolatedAsyncioTestCase):
    async def test_inactive_user_gets_activation(self):
        engine, db = _make_session()
        try:
            user = _bound_user(db)
            from app.services.legal.legal_workspace_service import legal_workspace_read_module

            messenger = AsyncMock()
            messenger.send_card.return_value = {"configured": True, "sent": True}
            with patch.object(feishu_service, "FeishuMessenger", return_value=messenger), \
                 patch.object(legal_workspace_read_module, "review_queue", return_value=[]), \
                 patch.object(feishu_service, "_reminder_due", return_value=True):
                result = await feishu_service.dispatch_feishu_reminders(db)
            self.assertEqual(result["sent_activation"], 1)
            self.assertEqual(result["sent_digest"], 0)
            card = messenger.send_card.call_args.args[1]
            self.assertEqual(card["header"]["title"]["content"], "欢迎使用律智检")
        finally:
            db.close()
            engine.dispose()

    async def test_active_user_gets_digest(self):
        engine, db = _make_session()
        try:
            user = _bound_user(db)
            _seed_activity(db, user.id)
            from app.services.legal.legal_workspace_service import legal_workspace_read_module

            messenger = AsyncMock()
            messenger.send_card.return_value = {"configured": True, "sent": True}
            with patch.object(feishu_service, "FeishuMessenger", return_value=messenger), \
                 patch.object(legal_workspace_read_module, "review_queue", return_value=[]), \
                 patch.object(feishu_service, "_reminder_due", return_value=True):
                result = await feishu_service.dispatch_feishu_reminders(db)
            self.assertEqual(result["sent_activation"], 0)
            self.assertEqual(result["sent_digest"], 1)
            card = messenger.send_card.call_args.args[1]
            self.assertEqual(card["header"]["title"]["content"], "律智检 · 周报回访")
        finally:
            db.close()
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
