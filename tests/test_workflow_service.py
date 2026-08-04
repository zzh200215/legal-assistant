import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.meeting import Meeting, MeetingSummary
from app.models.user import User
from app.schemas.workflow import MeetingTaskSelection
from app.services.workflow_service import workflow_service


class WorkflowServiceTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        self.db = SessionLocal()
        self.user = User(username="workflow_user", email="workflow@example.com", hashed_password="secret")
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)
        self.meeting = Meeting(user_id=self.user.id, title="项目例会", status="summarized")
        self.db.add(self.meeting)
        self.db.commit()
        self.db.add(
            MeetingSummary(
                meeting_id=self.meeting.id,
                action_items=json.dumps(
                    [
                        {"title": "确认上线范围", "assignee": "李明", "priority": "high", "evidence": "本周确认"},
                        {"title": "补充验收清单", "description": "完善验收项", "due_date": "2026-07-18"},
                    ],
                    ensure_ascii=False,
                ),
                risks=json.dumps([{"title": "依赖接口尚未联调", "description": "需跟进接口排期"}], ensure_ascii=False),
            )
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_meeting_task_preview_requires_confirmation_and_deduplicates(self):
        preview = workflow_service.preview_meeting_tasks(self.meeting.id, db=self.db, user=self.user)
        self.assertEqual(len(preview["items"]), 2)
        self.assertFalse(preview["items"][0]["already_created"])

        result = workflow_service.confirm_meeting_tasks(
            self.meeting.id,
            [MeetingTaskSelection(source_index=0), MeetingTaskSelection(source_index=1)],
            db=self.db,
            user=self.user,
        )
        self.assertEqual(len(result["created_tasks"]), 2)
        self.assertTrue(all(task["source_type"] == "meeting" for task in result["created_tasks"]))
        self.assertTrue(all(task["source_id"] == self.meeting.id for task in result["created_tasks"]))

        repeated = workflow_service.confirm_meeting_tasks(
            self.meeting.id,
            [MeetingTaskSelection(source_index=0), MeetingTaskSelection(source_index=1)],
            db=self.db,
            user=self.user,
        )
        self.assertEqual(repeated["created_tasks"], [])
        self.assertEqual(len(repeated["skipped_items"]), 2)

    def test_weekly_and_risk_drafts_keep_source_metadata(self):
        workflow_service.confirm_meeting_tasks(
            self.meeting.id,
            [MeetingTaskSelection(source_index=0), MeetingTaskSelection(source_index=1)],
            db=self.db,
            user=self.user,
        )
        report = workflow_service.create_weekly_report_draft(
            db=self.db,
            user=self.user,
            scope="mine",
            start_date=None,
            end_date=None,
            recipient="team@example.com",
            title=None,
        )
        report_metadata = json.loads(report.metadata_json)
        self.assertEqual(report.generation_type, "weekly_report")
        self.assertEqual(report_metadata["source_type"], "workflow_weekly_report")
        self.assertEqual(report_metadata["meeting_ids"], [self.meeting.id])
        self.assertIn("待推进事项", report.content)

        risk = workflow_service.create_risk_followup_draft(
            self.meeting.id,
            db=self.db,
            user=self.user,
            recipient=None,
        )
        risk_metadata = json.loads(risk.metadata_json)
        self.assertEqual(risk.generation_type, "risk_followup")
        self.assertEqual(risk_metadata["meeting_ids"], [self.meeting.id])
        self.assertIn("依赖接口尚未联调", risk.content)


if __name__ == "__main__":
    unittest.main()
