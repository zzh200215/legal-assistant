"""任务逾期升级：负责人/协作者逾期 3 天仍未完成时，给 owner 发一次站内 + 邮件提醒。"""

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import hash_password
from app.core.config import get_settings
from app.core.database import Base
from app.models.email import OutboundEmailPolicy
from app.models.legal_notifications import LegalNotificationEvent, LegalNotificationPreference
from app.models.org import Organization
from app.models.task import Task, TaskCollaborator
from app.models.user import User
from app.services.jobs.task_reminders import (
    ESCALATION_TEMPLATE, scan_task_due_reminders,
)
from app.services.notification.notification_service import notification_service

NOW = datetime(2026, 12, 30, 10, 0, 0)
OVERDUE_3D = datetime(2026, 12, 27, 23, 59, 59)
OVERDUE_1D = datetime(2026, 12, 29, 23, 59, 59)


class TaskOwnerEscalationTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        self.db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        self.org = Organization(name="逾期升级律所", code="task_owner_escalation")
        self.db.add(self.org)
        self.db.flush()
        self.owner = self._user("escalation-owner", full_name="王主任")
        self.assignee = self._user("escalation-assignee", full_name="张负责")
        self.collaborator = self._user("escalation-collab", full_name="李协作")
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _user(self, username, *, full_name=None):
        user = User(
            username=username,
            email=f"{username}@example.com",
            hashed_password=hash_password("pw"),
            full_name=full_name,
            organization_id=self.org.id,
        )
        self.db.add(user)
        self.db.flush()
        return user

    def _task(self, due_date, *, title="逾期任务", assignee_user_id=None, status="todo"):
        task = Task(
            user_id=self.owner.id,
            organization_id=self.org.id,
            title=title,
            status=status,
            priority="medium",
            progress=0,
            due_date=due_date,
            assignee_user_id=assignee_user_id,
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def _collaborate(self, task, user):
        self.db.add(TaskCollaborator(task_id=task.id, user_id=user.id))
        self.db.commit()

    def _events(self, task):
        return (
            self.db.query(LegalNotificationEvent)
            .filter(
                LegalNotificationEvent.reference_type == "task",
                LegalNotificationEvent.reference_id == task.id,
            )
            .all()
        )

    def _site_events(self, task):
        return {event.user_id: event for event in self._events(task) if event.channel == "site"}

    def test_owner_escalated_when_assignee_overdue_three_days(self):
        task = self._task(OVERDUE_3D, assignee_user_id=self.assignee.id)
        self._collaborate(task, self.collaborator)

        result = scan_task_due_reminders(self.db, now=NOW)

        self.assertEqual(result["created"], 3)
        self.assertEqual(result["overdue_3d"], 2)
        self.assertEqual(result["collaborator_created"], 1)
        self.assertEqual(result["escalated"], 1)
        events = self._site_events(task)
        self.assertEqual(
            set(events), {self.owner.id, self.assignee.id, self.collaborator.id}
        )
        escalation = events[self.owner.id]
        self.assertEqual(escalation.template_key, ESCALATION_TEMPLATE)
        self.assertIn("任务逾期 3 天升级", escalation.title)
        self.assertIn("负责人 张负责", escalation.body)
        self.assertIn("协作者 1 人", escalation.body)
        self.assertEqual(events[self.assignee.id].template_key, "task_overdue_3d")
        self.assertEqual(
            events[self.collaborator.id].template_key, "task_collaborator_overdue_3d"
        )

    def test_escalation_sent_once_per_task(self):
        task = self._task(OVERDUE_3D, assignee_user_id=self.assignee.id)

        scan_task_due_reminders(self.db, now=NOW)
        second = scan_task_due_reminders(self.db, now=NOW)

        self.assertEqual(second["created"], 0)
        self.assertEqual(second["escalated"], 0)
        escalations = [
            event for event in self._events(task) if event.template_key == ESCALATION_TEMPLATE
        ]
        self.assertEqual(len(escalations), 1)

    def test_no_escalation_before_three_days(self):
        task = self._task(OVERDUE_1D, assignee_user_id=self.assignee.id)

        result = scan_task_due_reminders(self.db, now=NOW)

        self.assertEqual(result["escalated"], 0)
        self.assertEqual(result["overdue_1d"], 1)
        self.assertNotIn(self.owner.id, self._site_events(task))

    def test_no_escalation_when_owner_is_assignee(self):
        task = self._task(OVERDUE_3D, assignee_user_id=self.owner.id)

        result = scan_task_due_reminders(self.db, now=NOW)

        self.assertEqual(result["escalated"], 0)
        self.assertEqual(self._site_events(task)[self.owner.id].template_key, "task_overdue_3d")

    def test_no_escalation_when_owner_already_primary_recipient(self):
        task = self._task(OVERDUE_3D)
        self._collaborate(task, self.collaborator)

        result = scan_task_due_reminders(self.db, now=NOW)

        self.assertEqual(result["escalated"], 0)
        events = self._site_events(task)
        self.assertEqual(events[self.owner.id].template_key, "task_overdue_3d")
        self.assertEqual(
            events[self.collaborator.id].template_key, "task_collaborator_overdue_3d"
        )

    def test_no_escalation_when_owner_is_collaborator(self):
        task = self._task(OVERDUE_3D, assignee_user_id=self.assignee.id)
        self._collaborate(task, self.owner)

        result = scan_task_due_reminders(self.db, now=NOW)

        self.assertEqual(result["escalated"], 0)
        events = self._site_events(task)
        self.assertEqual(
            events[self.owner.id].template_key, "task_collaborator_overdue_3d"
        )

    def test_owner_timezone_controls_escalation_day(self):
        for user, zone in ((self.owner, "Asia/Tokyo"), (self.assignee, "America/New_York")):
            self.db.add(
                LegalNotificationPreference(
                    user_id=user.id,
                    organization_id=self.org.id,
                    event_type="deadline",
                    channels_json='["site"]',
                    timezone=zone,
                )
            )
        self.db.commit()
        task = self._task(datetime(2026, 12, 28, 23, 59, 59), assignee_user_id=self.assignee.id)
        # UTC 2026-12-31 02:00 = 东京 12-31 11:00 / 纽约 12-30 21:00
        result = scan_task_due_reminders(
            self.db, now=datetime(2026, 12, 31, 2, 0, tzinfo=timezone.utc)
        )

        self.assertEqual(result["escalated"], 1)
        self.assertEqual(result["overdue_3d"], 0)
        self.assertEqual(result["created"], 1)
        self.assertIn("任务逾期 3 天升级", self._site_events(task)[self.owner.id].title)

    def test_escalation_email_uses_builtin_template_and_task_deeplink(self):
        self.db.add(OutboundEmailPolicy(organization_id=self.org.id, enabled=True))
        self.db.commit()
        task = self._task(OVERDUE_3D, assignee_user_id=self.assignee.id)

        scan_task_due_reminders(self.db, now=NOW)

        email = (
            self.db.query(LegalNotificationEvent)
            .filter(
                LegalNotificationEvent.reference_id == task.id,
                LegalNotificationEvent.user_id == self.owner.id,
                LegalNotificationEvent.channel == "email",
            )
            .one()
        )
        self.assertEqual(email.template_key, ESCALATION_TEMPLATE)
        with patch.object(
            get_settings(), "NOTIFICATION_EMAIL_ACTION_BASE_URL", "https://legal.example.com/"
        ):
            subject, text, html = notification_service._render_email_content(self.db, email)
        self.assertIn("已逾期 3 天以上", html)
        self.assertIn("已逾期 3 天以上", text)
        self.assertIn("负责人 张负责 仍未完成", email.body)
        self.assertNotIn("协作者 0 人", email.body)
        self.assertIn("https://legal.example.com/tasks?taskId=" + str(task.id), html)
        self.assertIn("去查看", text)

    def test_done_or_archived_task_not_escalated(self):
        done = self._task(OVERDUE_3D, assignee_user_id=self.assignee.id, status="done")
        archived = self._task(OVERDUE_3D, assignee_user_id=self.assignee.id)
        archived.archived_at = datetime(2026, 12, 30, 9, 0, 0)
        self.db.commit()

        result = scan_task_due_reminders(self.db, now=NOW)

        self.assertEqual(result["escalated"], 0)
        self.assertEqual(self._events(done), [])
        self.assertEqual(self._events(archived), [])


if __name__ == "__main__":
    unittest.main()
