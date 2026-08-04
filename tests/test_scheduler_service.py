import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.connector import MailboxMessage
from app.models.task import Task
from app.models.user import User
from app.schemas.schedule import ScheduledWorkflowCreate, ScheduledWorkflowUpdate
from app.services.scheduler_service import scheduler_service


class SchedulerServiceTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        self.db = SessionLocal()
        self.user = User(username="schedule_user", email="schedule@example.com", hashed_password="secret")
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

    def tearDown(self):
        self.db.close()

    def test_manual_daily_digest_creates_internal_draft(self):
        self.db.add(
            MailboxMessage(
                connector_id=1,
                user_id=self.user.id,
                message_uid="100",
                mailbox="INBOX",
                subject="紧急确认发布范围",
                sender="manager@example.com",
                summary="请在今天确认发布范围。",
                category="action",
                importance="high",
            )
        )
        self.db.commit()
        schedule = scheduler_service.create_schedule(
            db=self.db,
            user=self.user,
            request=ScheduledWorkflowCreate(
                name="每日邮件摘要",
                workflow_type="daily_mail_digest",
                frequency="daily",
                run_time="09:30",
                config={},
            ),
        )
        execution = scheduler_service.start_manual_run(schedule.id, db=self.db, user=self.user)
        completed = scheduler_service.execute(execution.id, db=self.db)
        self.assertEqual(completed.status, "succeeded")
        self.assertIn("草稿", completed.result_summary)
        self.assertEqual(scheduler_service.execute(execution.id, db=self.db).id, completed.id)

    def test_due_dispatch_is_idempotent_and_pause_stops_future_runs(self):
        self.db.add(Task(user_id=self.user.id, title="整理周报", status="todo", priority="medium"))
        self.db.commit()
        schedule = scheduler_service.create_schedule(
            db=self.db,
            user=self.user,
            request=ScheduledWorkflowCreate(
                name="每周项目周报",
                workflow_type="weekly_report",
                frequency="weekly",
                weekday=0,
                run_time="17:00",
                config={"scope": "mine"},
            ),
        )
        schedule.next_run_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        self.db.commit()
        first = scheduler_service.dispatch_due(db=self.db, now=datetime.now(timezone.utc))
        second = scheduler_service.dispatch_due(db=self.db, now=datetime.now(timezone.utc))
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        completed = scheduler_service.execute(first[0], db=self.db)
        self.assertEqual(completed.status, "succeeded")

        paused = scheduler_service.update_schedule(
            schedule.id, db=self.db, user=self.user, request=ScheduledWorkflowUpdate(enabled=False)
        )
        self.assertFalse(paused.enabled)
        self.assertIsNone(paused.next_run_at)


if __name__ == "__main__":
    unittest.main()
