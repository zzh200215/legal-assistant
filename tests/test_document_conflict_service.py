import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.user import User
from app.services.documents.document_conflict_service import document_conflict_service


class DocumentConflictServiceTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        self.db = self.SessionLocal()
        self.user = User(username="conflict_user", email="conflict@example.com", hashed_password="secret")
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

    def tearDown(self):
        self.db.close()

    def test_confirmed_conflict_becomes_traceable_task_and_can_be_resolved(self):
        conflict = {
            "field_label": "日期",
            "field": "上线日期",
            "severity": "high",
            "evidence_complete": True,
            "recommended_action": "确认最终上线日期。",
            "source_a": {"document_title": "合同", "value": "2026-08-01", "source_text": "上线日期为 2026-08-01", "page_number": 3, "section_title": "里程碑"},
            "source_b": {"document_title": "会议纪要", "value": "2026-08-15", "source_text": "会议确认 2026-08-15 上线", "page_number": 1, "section_title": "决策"},
        }

        suggestions = document_conflict_service.create_suggestions([conflict], document_ids=[11, 12], db=self.db, user=self.user)
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["status"], "pending_confirmation")

        confirmed = document_conflict_service.confirm_task(suggestions[0]["id"], db=self.db, user=self.user, title=None, assignee="李明", priority=None)
        self.assertEqual(confirmed["case"]["status"], "task_created")
        self.assertEqual(confirmed["task"]["source_type"], "document_conflict")
        self.assertEqual(confirmed["task"]["source_id"], suggestions[0]["id"])
        self.assertIn("原文 A", confirmed["task"]["description"])
        self.assertIn("原文 B", confirmed["task"]["description"])

        resolved = document_conflict_service.update_status(suggestions[0]["id"], db=self.db, user=self.user, status="resolved", resolution_note="已以最新会议决策为准")
        self.assertEqual(resolved["status"], "resolved")
        self.assertIsNotNone(resolved["resolved_at"])

    def test_incomplete_evidence_cannot_create_suggestion(self):
        created = document_conflict_service.create_suggestions(
            [{"field": "金额", "evidence_complete": False}], document_ids=[1, 2], db=self.db, user=self.user
        )

        self.assertEqual(created, [])
