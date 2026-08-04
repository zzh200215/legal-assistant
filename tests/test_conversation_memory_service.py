import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.chat import ChatMessage, ChatSession, ChatSessionMemory
from app.models.user import User
from app.services.agent_service import AgentService
from app.services.conversation_memory_service import conversation_memory_service


class ConversationMemoryServiceTests(unittest.IsolatedAsyncioTestCase):
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
        self.user = User(username="memory-user", email="memory-user@example.com", hashed_password="secret")
        self.other_user = User(username="other-user", email="other-user@example.com", hashed_password="secret")
        self.db.add_all([self.user, self.other_user])
        self.db.commit()
        self.db.refresh(self.user)
        self.db.refresh(self.other_user)
        self.settings = conversation_memory_service.settings
        self.original_recent = self.settings.CONVERSATION_MEMORY_RECENT_MESSAGES
        self.original_trigger = self.settings.CONVERSATION_MEMORY_SUMMARY_TRIGGER
        self.original_auto_preference = self.settings.CONVERSATION_MEMORY_AUTO_PREFERENCE_ENABLED
        self.settings.CONVERSATION_MEMORY_RECENT_MESSAGES = 4
        self.settings.CONVERSATION_MEMORY_SUMMARY_TRIGGER = 8
        self.settings.CONVERSATION_MEMORY_AUTO_PREFERENCE_ENABLED = True

    def tearDown(self):
        self.settings.CONVERSATION_MEMORY_RECENT_MESSAGES = self.original_recent
        self.settings.CONVERSATION_MEMORY_SUMMARY_TRIGGER = self.original_trigger
        self.settings.CONVERSATION_MEMORY_AUTO_PREFERENCE_ENABLED = self.original_auto_preference
        self.db.close()

    def _session(self, user_id: int, session_type: str = "general") -> ChatSession:
        session = ChatSession(user_id=user_id, title="memory", session_type=session_type)
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        return session

    def _messages(self, session_id: int, count: int) -> None:
        for index in range(count):
            self.db.add(
                ChatMessage(
                    session_id=session_id,
                    role="user" if index % 2 == 0 else "assistant",
                    content=f"message-{index}",
                )
            )
        self.db.commit()

    def test_context_is_bounded_and_injects_only_owned_summary_and_preferences(self):
        session = self._session(self.user.id)
        self._messages(session.id, 7)
        self.db.add(
            ChatSessionMemory(
                session_id=session.id,
                user_id=self.user.id,
                summary="用户正在准备合同风险汇总。",
                summarized_through_message_id=3,
            )
        )
        self.db.commit()
        conversation_memory_service.save_explicit_preference(
            self.db,
            self.user.id,
            category="writing",
            preference_key="tone",
            preference_value="结论优先，使用简洁中文。",
        )

        messages = conversation_memory_service.build_chat_messages(self.db, self.user.id, session.id)

        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("合同风险汇总", messages[0]["content"])
        self.assertIn("结论优先", messages[0]["content"])
        self.assertEqual([item["content"] for item in messages[1:]], ["message-3", "message-4", "message-5", "message-6"])
        self.assertNotIn("message-0", "\n".join(item["content"] for item in messages))

        other_context = conversation_memory_service.build_agent_context(self.db, self.other_user.id, None)
        self.assertEqual(other_context, "")

    async def test_compaction_keeps_recent_messages_and_marks_progress(self):
        session = self._session(self.user.id)
        self._messages(session.id, 10)
        with patch(
            "app.services.conversation_memory_service.llm_service.generate",
            new=AsyncMock(
                side_effect=[
                    "- 用户需要一份风险结论\n- 后续需要生成邮件草稿",
                    '{"preferences":[{"category":"writing","preference_key":"tone","preference_value":"结论优先，使用简洁中文。"}]}',
                ]
            ),
        ) as generate:
            compacted = await conversation_memory_service.compact_session_if_needed(self.db, self.user.id, session.id)

        self.assertTrue(compacted)
        self.assertEqual(generate.await_count, 2)
        memory = conversation_memory_service.get_session_memory(self.db, self.user.id, session.id)
        self.assertIsNotNone(memory)
        self.assertIn("风险结论", memory.summary)
        self.assertEqual(memory.summarized_through_message_id, 6)
        inferred = conversation_memory_service.list_preferences(self.db, self.user.id)
        self.assertEqual([(item.source, item.preference_key) for item in inferred], [("inferred", "tone")])

        messages = conversation_memory_service.build_chat_messages(self.db, self.user.id, session.id)
        self.assertEqual([item["content"] for item in messages[1:]], ["message-6", "message-7", "message-8", "message-9"])

    async def test_document_rag_session_is_not_compacted(self):
        session = self._session(self.user.id, session_type="document_rag")
        self._messages(session.id, 10)
        with patch("app.services.conversation_memory_service.llm_service.generate", new=AsyncMock()) as generate:
            compacted = await conversation_memory_service.compact_session_if_needed(self.db, self.user.id, session.id)
        self.assertFalse(compacted)
        generate.assert_not_awaited()

    def test_agent_memory_is_marked_as_non_evidence(self):
        session = self._session(self.user.id)
        self.db.add(ChatSessionMemory(session_id=session.id, user_id=self.user.id, summary="本周优先完成项目复盘。"))
        self.db.commit()
        context = conversation_memory_service.build_agent_context(self.db, self.user.id, session.id)
        messages = AgentService()._build_worker_messages(
            "整理会议纪要",
            "meeting_agent",
            self.user.id,
            memory_context=context,
        )
        self.assertIn("不是工具输入、事实证据或权限依据", messages[1]["content"])
        self.assertIn("项目复盘", messages[1]["content"])

    async def test_sensitive_auto_memory_is_discarded(self):
        session = self._session(self.user.id)
        self.db.add(ChatSessionMemory(session_id=session.id, user_id=self.user.id, summary="用户提到手机号和身份证信息。"))
        self.db.commit()
        with patch(
            "app.services.conversation_memory_service.llm_service.generate",
            new=AsyncMock(
                return_value='{"preferences":[{"category":"general","preference_key":"contact","preference_value":"手机号 13800138000"}]}'
            ),
        ):
            saved = await conversation_memory_service.extract_long_term_preferences(self.db, self.user.id, session.id)
        self.assertEqual(saved, [])


if __name__ == "__main__":
    unittest.main()
