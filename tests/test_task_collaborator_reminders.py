"""任务到期提醒的协作者收件人：负责人 + 协作者、按人时区、按人去重、协作者邮件文案。"""

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
from app.services.jobs.task_reminders import scan_task_due_reminders
from app.services.notification.notification_service import notification_service

NOW = datetime(2026, 12, 30, 10, 0, 0)
TOMORROW = datetime(2026, 12, 31, 23, 59, 59)
TODAY = datetime(2026, 12, 30, 23, 59, 59)


class TaskCollaboratorReminderTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        self.db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        self.org = Organization(name="协作者提醒律所", code="task_collab_remind")
        self.db.add(self.org)
        self.db.flush()
        self.owner = self._user("collab-owner")
        self.assignee = self._user("collab-assignee")
        self.collaborator = self._user("collab-member")
        self.other = self._user("collab-other")
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _user(self, username):
        user = User(
            username=username,
            email=f"{username}@example.com",
            hashed_password=hash_password("pw"),
            organization_id=self.org.id,
        )
        self.db.add(user)
        self.db.flush()
        return user

    def _task(self, due_date, *, title="协作任务", assignee_user_id=None, status="todo"):
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

    def _by_user(self, task):
        return {event.user_id: event for event in self._events(task) if event.channel == "site"}

    def test_assignee_and_collaborator_both_notified_with_distinct_templates(self):
        task = self._task(TODAY, assignee_user_id=self.assignee.id)
        self._collaborate(task, self.collaborator)

        result = scan_task_due_reminders(self.db, now=NOW)

        self.assertEqual(result["created"], 2)
        self.assertEqual(result["due_today"], 2)
        self.assertEqual(result["collaborator_created"], 1)
        events = self._by_user(task)
        self.assertEqual(set(events), {self.assignee.id, self.collaborator.id})
        self.assertEqual(events[self.assignee.id].template_key, "task_due_today")
        self.assertEqual(events[self.collaborator.id].template_key, "task_collaborator_due_today")
        self.assertIn("协作者提醒", events[self.collaborator.id].title)
        self.assertNotIn("协作者提醒", events[self.assignee.id].title)
        self.assertIn("任务今天到期", events[self.collaborator.id].title)

    def test_owner_not_notified_when_assignee_exists(self):
        task = self._task(TODAY, assignee_user_id=self.assignee.id)
        self._collaborate(task, self.collaborator)

        scan_task_due_reminders(self.db, now=NOW)

        events = self._by_user(task)
        self.assertNotIn(self.owner.id, events)
        self.assertIn(self.assignee.id, events)

    def test_owner_is_fallback_recipient_when_no_assignee(self):
        task = self._task(TODAY)
        self._collaborate(task, self.collaborator)

        result = scan_task_due_reminders(self.db, now=NOW)

        self.assertEqual(result["created"], 2)
        events = self._by_user(task)
        self.assertEqual(events[self.owner.id].template_key, "task_due_today")
        self.assertEqual(events[self.collaborator.id].template_key, "task_collaborator_due_today")

    def test_same_user_as_assignee_and_collaborator_notified_once(self):
        task = self._task(TODAY, assignee_user_id=self.assignee.id)
        self._collaborate(task, self.assignee)

        result = scan_task_due_reminders(self.db, now=NOW)

        self.assertEqual(result["created"], 1)
        self.assertEqual(result["collaborator_created"], 0)
        events = self._by_user(task)
        self.assertEqual(set(events), {self.assignee.id})
        self.assertEqual(events[self.assignee.id].template_key, "task_due_today")

    def test_owner_added_as_collaborator_notified_once_as_owner(self):
        task = self._task(TODAY)
        self._collaborate(task, self.owner)

        result = scan_task_due_reminders(self.db, now=NOW)

        self.assertEqual(result["created"], 1)
        self.assertEqual(result["collaborator_created"], 0)
        self.assertEqual(self._by_user(task)[self.owner.id].template_key, "task_due_today")

    def test_dedupe_per_recipient_and_stage(self):
        task = self._task(TOMORROW, assignee_user_id=self.assignee.id)
        self._collaborate(task, self.collaborator)

        first = scan_task_due_reminders(self.db, now=NOW)
        self.assertEqual(first["created"], 2)
        self.assertEqual(first["due_tomorrow"], 2)
        self.assertEqual(first["collaborator_created"], 1)

        second = scan_task_due_reminders(self.db, now=NOW)
        self.assertEqual(second["created"], 0)
        self.assertEqual(len(self._by_user(task)), 2)

        # 阶段变化：明天 → 今天，负责人和协作者都各再收到一次
        task.due_date = TODAY
        self.db.commit()
        third = scan_task_due_reminders(self.db, now=NOW)
        self.assertEqual(third["created"], 2)
        self.assertEqual(third["due_today"], 2)
        self.assertEqual(third["collaborator_created"], 1)
        stages = sorted(
            event.template_key for event in self._events(task) if event.channel == "site"
        )
        self.assertEqual(
            stages,
            [
                "task_collaborator_due_today",
                "task_collaborator_due_tomorrow",
                "task_due_today",
                "task_due_tomorrow",
            ],
        )

    def test_collaborator_timezone_controls_own_stage(self):
        self.db.add(
            LegalNotificationPreference(
                user_id=self.collaborator.id,
                organization_id=self.org.id,
                event_type="deadline",
                channels_json='["site"]',
                timezone="America/New_York",
            )
        )
        self.db.commit()
        task = self._task(datetime(2026, 12, 31, 23, 59, 59), assignee_user_id=self.assignee.id)
        self._collaborate(task, self.collaborator)
        # UTC 2026-12-31 02:00 = 上海 12-31 10:00 / 纽约 12-30 21:00
        now = datetime(2026, 12, 31, 2, 0, tzinfo=timezone.utc)

        result = scan_task_due_reminders(self.db, now=now)

        self.assertEqual(result["due_today"], 1)
        self.assertEqual(result["due_tomorrow"], 1)
        events = self._by_user(task)
        self.assertEqual(events[self.assignee.id].template_key, "task_due_today")
        self.assertEqual(
            events[self.collaborator.id].template_key, "task_collaborator_due_tomorrow"
        )

    def test_collaborator_email_uses_builtin_template_and_task_deeplink(self):
        self.db.add(OutboundEmailPolicy(organization_id=self.org.id, enabled=True))
        self.db.commit()
        task = self._task(TODAY, assignee_user_id=self.assignee.id)
        self._collaborate(task, self.collaborator)

        scan_task_due_reminders(self.db, now=NOW)

        email = (
            self.db.query(LegalNotificationEvent)
            .filter(
                LegalNotificationEvent.reference_id == task.id,
                LegalNotificationEvent.user_id == self.collaborator.id,
                LegalNotificationEvent.channel == "email",
            )
            .one()
        )
        self.assertEqual(email.template_key, "task_collaborator_due_today")
        with patch.object(
            get_settings(), "NOTIFICATION_EMAIL_ACTION_BASE_URL", "https://legal.example.com/"
        ):
            subject, text, html = notification_service._render_email_content(self.db, email)
        self.assertIn("你参与的任务今天到期", html)
        self.assertIn("你参与的任务今天到期", text)
        self.assertIn("https://legal.example.com/tasks?taskId=" + str(task.id), html)
        self.assertIn("去处理", text)

    def test_done_or_archived_task_skips_collaborator(self):
        done = self._task(TODAY, assignee_user_id=self.assignee.id, status="done")
        self._collaborate(done, self.collaborator)
        archived = self._task(TODAY, assignee_user_id=self.assignee.id)
        self._collaborate(archived, self.collaborator)
        archived.archived_at = datetime(2026, 12, 30, 9, 0, 0)
        self.db.commit()

        result = scan_task_due_reminders(self.db, now=NOW)

        self.assertEqual(result["created"], 0)
        self.assertEqual(self._events(done), [])
        self.assertEqual(self._events(archived), [])


if __name__ == "__main__":
    unittest.main()
