import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.connector import ConnectorSyncJob, ExternalConnector
from app.models.email import EmailDraft, EmailSendRequest
from app.models.schedule import ScheduledWorkflow, WorkflowExecution
from app.models.user import User
from app.services.analytics_service import analytics_service


class OperationsAlertTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:", future=True,
            connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        self.db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        Base.metadata.create_all(bind=engine)
        self.user = User(username="ops_user", email="ops@example.com", hashed_password="secret")
        self.other = User(username="other_user", email="other@example.com", hashed_password="secret")
        self.db.add_all([self.user, self.other])
        self.db.commit()
        self.db.refresh(self.user)
        self.db.refresh(self.other)

    def tearDown(self):
        self.db.close()

    def test_office_automation_failures_and_pending_approval_become_alerts(self):
        now = datetime.utcnow()
        schedule = ScheduledWorkflow(
            user_id=self.user.id, name="每日摘要", workflow_type="daily_mail_digest",
            frequency="daily", run_time="09:00", enabled=True, next_run_at=now - timedelta(minutes=20),
        )
        self.db.add(schedule)
        self.db.commit()
        self.db.add(WorkflowExecution(
            schedule_id=schedule.id, user_id=self.user.id, trigger_type="scheduled",
            idempotency_key="failed-run", status="failed", error_message="worker unavailable", created_at=now,
        ))
        connector = ExternalConnector(user_id=self.user.id, connector_type="imap_mailbox", name="企业邮箱", status="active")
        self.db.add(connector)
        self.db.commit()
        self.db.add(ConnectorSyncJob(connector_id=connector.id, user_id=self.user.id, status="failed", error_message="connection refused", created_at=now))
        draft = EmailDraft(user_id=self.user.id, subject="项目更新", recipient="team@example.com", content="正文", status="draft", generation_type="generate", tone="professional")
        self.db.add(draft)
        self.db.commit()
        self.db.add_all([
            EmailSendRequest(draft_id=draft.id, smtp_connector_id=connector.id, user_id=self.user.id, recipient="team@example.com", subject="项目更新", content_hash="a" * 64, idempotency_key="smtp-failed", status="failed", created_at=now),
            EmailSendRequest(draft_id=draft.id, smtp_connector_id=connector.id, user_id=self.user.id, recipient="team@example.com", subject="待审批", content_hash="b" * 64, idempotency_key="smtp-pending", status="pending", created_at=now - timedelta(hours=25)),
        ])
        self.db.commit()

        alerts = analytics_service.list_alerts(db=self.db, user_id=self.user.id, days=7, limit=100)
        categories = {item["category"] for item in alerts}
        self.assertTrue({"scheduler_error", "scheduler_delay", "mailbox_sync_error", "outbound_email_error", "approval_pending"}.issubset(categories))
        self.assertNotIn("team@example.com", " ".join(item["message"] for item in alerts))
        stats = analytics_service.get_alert_stats(db=self.db, user_id=self.user.id, days=7)
        self.assertEqual(stats["by_source"]["scheduler"], 2)
        self.assertEqual(stats["by_source"]["outbound_email"], 2)

    def test_user_scope_excludes_other_users_schedule_failures(self):
        schedule = ScheduledWorkflow(
            user_id=self.other.id, name="其他计划", workflow_type="weekly_report",
            frequency="weekly", run_time="17:00", enabled=True,
        )
        self.db.add(schedule)
        self.db.commit()
        self.db.add(WorkflowExecution(
            schedule_id=schedule.id, user_id=self.other.id, trigger_type="manual",
            idempotency_key="other-failed-run", status="failed", error_message="failed", created_at=datetime.utcnow(),
        ))
        self.db.commit()
        self.assertEqual(analytics_service.list_alerts(db=self.db, user_id=self.user.id, days=7), [])


if __name__ == "__main__":
    unittest.main()
