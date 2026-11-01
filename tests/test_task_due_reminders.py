"""任务到期/逾期提醒：四个阶段各一次，按 owner 时区算日期，站内 + 可选邮件。"""

import unittest
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import hash_password
from app.core.celery_app import celery_app
from app.core.database import Base
from app.models.legal import LegalCase
from app.models.legal_notifications import LegalNotificationEvent, LegalNotificationPreference
from app.models.org import Organization
from app.models.task import Task
from app.models.user import User
from app.services.jobs.task_reminders import scan_task_due_reminders

NOW = datetime(2026, 12, 30, 10, 0, 0)
TOMORROW = datetime(2026, 12, 31, 23, 59, 59)
TODAY = datetime(2026, 12, 30, 23, 59, 59)


class TaskDueReminderTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        self.db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        self.org = Organization(name="任务提醒律所", code="task_remind_firm")
        self.db.add(self.org)
        self.db.flush()
        self.user = User(
            username="task-reminder",
            email="task-reminder@example.com",
            hashed_password=hash_password("pw"),
            organization_id=self.org.id,
        )
        self.db.add(self.user)
        self.db.flush()
        self.case = LegalCase(
            title="提醒案件", case_type="other", organization_id=self.org.id, user_id=self.user.id
        )
        self.db.add(self.case)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _task(self, due_date, *, status="todo", title="到期任务", case_id=None, org_id="default"):
        task = Task(
            user_id=self.user.id,
            organization_id=self.org.id if org_id == "default" else org_id,
            case_id=case_id,
            title=title,
            status=status,
            priority="medium",
            progress=0,
            due_date=due_date,
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def _site_events(self):
        return (
            self.db.query(LegalNotificationEvent)
            .filter(LegalNotificationEvent.channel == "site")
            .all()
        )

    def test_sends_all_stages_once(self):
        tomorrow = self._task(TOMORROW, title="明天到期的任务")
        today = self._task(TODAY, title="今天到期的任务")
        overdue_one = self._task(datetime(2026, 12, 29, 23, 59, 59), title="逾期一天")
        overdue_three = self._task(datetime(2026, 12, 27, 23, 59, 59), title="逾期三天")
        self._task(datetime(2026, 12, 28, 23, 59, 59), title="逾期两天不提醒")
        self._task(datetime(2027, 1, 5, 23, 59, 59), title="还早")
        self._task(TOMORROW, status="done", title="已完成")
        self._task(TODAY, status="cancelled", title="已取消")
        self._task(None, title="无截止日期")
        archived = self._task(TODAY, title="已归档")
        archived.archived_at = datetime(2026, 12, 30, 9, 0, 0)
        self.db.commit()

        result = scan_task_due_reminders(self.db, now=NOW)

        self.assertEqual(
            result,
            {
                "created": 4,
                "due_tomorrow": 1,
                "due_today": 1,
                "overdue_1d": 1,
                "overdue_3d": 1,
                "skipped_no_org": 0,
            },
        )
        events = {event.reference_id: event for event in self._site_events()}
        self.assertEqual(
            set(events), {tomorrow.id, today.id, overdue_one.id, overdue_three.id}
        )
        self.assertEqual(events[tomorrow.id].template_key, "task_due_tomorrow")
        self.assertIn("任务明天到期", events[tomorrow.id].title)
        self.assertIn("task:tomorrow", events[tomorrow.id].body)
        self.assertEqual(events[tomorrow.id].status, "delivered")
        self.assertEqual(events[today.id].template_key, "task_due_today")
        self.assertIn("任务今天到期", events[today.id].title)
        self.assertEqual(events[today.id].user_id, self.user.id)
        self.assertEqual(events[today.id].organization_id, self.org.id)
        self.assertEqual(events[overdue_one.id].template_key, "task_overdue_1d")
        self.assertIn("任务已逾期 1 天", events[overdue_one.id].title)
        self.assertIn("已逾期 1 天", events[overdue_one.id].body)
        self.assertEqual(events[overdue_three.id].template_key, "task_overdue_3d")
        self.assertIn("任务已逾期 3 天", events[overdue_three.id].title)

        # 幂等：再扫一次不重复
        self.assertEqual(
            scan_task_due_reminders(self.db, now=NOW),
            {
                "created": 0,
                "due_tomorrow": 0,
                "due_today": 0,
                "overdue_1d": 0,
                "overdue_3d": 0,
                "skipped_no_org": 0,
            },
        )
        self.assertEqual(len(self._site_events()), 4)

    def test_stage_change_from_tomorrow_to_today_notifies_again(self):
        task = self._task(TOMORROW, title="先明天后今天")
        scan_task_due_reminders(self.db, now=NOW)

        task.due_date = TODAY
        self.db.commit()
        result = scan_task_due_reminders(self.db, now=NOW)

        self.assertEqual(result["due_today"], 1)
        stages = [
            event.template_key
            for event in self._site_events()
            if event.reference_id == task.id
        ]
        self.assertEqual(sorted(stages), ["task_due_today", "task_due_tomorrow"])

    def test_overdue_reminders_follow_due_date(self):
        overdue_one = self._task(datetime(2026, 12, 29, 23, 59, 59), title="逾期一天")
        overdue_three = self._task(datetime(2026, 12, 27, 23, 59, 59), title="逾期三天")
        self._task(datetime(2026, 12, 28, 23, 59, 59), title="逾期两天")
        self._task(datetime(2026, 12, 26, 23, 59, 59), title="逾期四天")

        result = scan_task_due_reminders(self.db, now=NOW)

        self.assertEqual(result["overdue_1d"], 1)
        self.assertEqual(result["overdue_3d"], 1)
        events = {event.reference_id: event for event in self._site_events()}
        self.assertEqual(events[overdue_one.id].template_key, "task_overdue_1d")
        self.assertIn("已逾期 1 天", events[overdue_one.id].body)
        self.assertEqual(events[overdue_three.id].template_key, "task_overdue_3d")
        self.assertIn("已逾期 3 天", events[overdue_three.id].body)

    def test_user_timezone_controls_today_and_tomorrow(self):
        self.db.add(
            LegalNotificationPreference(
                user_id=self.user.id,
                organization_id=self.org.id,
                event_type="deadline",
                channels_json='["site"]',
                timezone="America/New_York",
            )
        )
        self.db.commit()
        # UTC 2026-12-31 02:00 = 纽约 2026-12-30 21:00（上海已是 12-31 10:00）
        now = datetime(2026, 12, 31, 2, 0, tzinfo=timezone.utc)
        today = self._task(datetime(2026, 12, 30, 23, 59, 59), title="纽约今天")
        tomorrow = self._task(datetime(2026, 12, 31, 23, 59, 59), title="纽约明天")
        overdue = self._task(datetime(2026, 12, 29, 23, 59, 59), title="纽约逾期一天")

        result = scan_task_due_reminders(self.db, now=now)

        self.assertEqual(result["due_today"], 1)
        self.assertEqual(result["due_tomorrow"], 1)
        self.assertEqual(result["overdue_1d"], 1)
        events = {event.reference_id: event for event in self._site_events()}
        self.assertEqual(events[today.id].template_key, "task_due_today")
        self.assertEqual(events[tomorrow.id].template_key, "task_due_tomorrow")
        self.assertEqual(events[overdue.id].template_key, "task_overdue_1d")

    def test_email_channel_created_when_org_has_outbound_policy(self):
        from app.models.email import OutboundEmailPolicy

        self.db.add(OutboundEmailPolicy(organization_id=self.org.id, enabled=True))
        self.db.commit()
        task = self._task(TODAY, title="邮件提醒任务")

        result = scan_task_due_reminders(self.db, now=NOW)

        self.assertEqual(result["due_today"], 1)
        events = (
            self.db.query(LegalNotificationEvent)
            .filter(LegalNotificationEvent.reference_id == task.id)
            .all()
        )
        self.assertEqual(
            sorted((event.channel, event.status) for event in events),
            [("email", "pending"), ("site", "delivered")],
        )
        self.assertTrue(all(event.template_key == "task_due_today" for event in events))
        self.assertEqual(scan_task_due_reminders(self.db, now=NOW)["created"], 0)

    def test_skips_task_without_organization(self):
        user = User(
            username="no-org",
            email="no-org@example.com",
            hashed_password=hash_password("pw"),
            organization_id=None,
        )
        self.db.add(user)
        self.db.commit()
        self.db.add(
            Task(
                user_id=user.id,
                organization_id=None,
                title="无组织任务",
                status="todo",
                priority="medium",
                progress=0,
                due_date=TODAY,
            )
        )
        self.db.commit()

        result = scan_task_due_reminders(self.db, now=NOW)

        self.assertEqual(result["created"], 0)
        self.assertEqual(result["skipped_no_org"], 1)

    def test_case_id_preserved_and_org_falls_back_to_user(self):
        task = self._task(TODAY, title="案件任务", case_id=self.case.id, org_id=None)

        scan_task_due_reminders(self.db, now=NOW)

        event = (
            self.db.query(LegalNotificationEvent)
            .filter(
                LegalNotificationEvent.reference_type == "task",
                LegalNotificationEvent.reference_id == task.id,
            )
            .one()
        )
        self.assertEqual(event.case_id, self.case.id)
        self.assertEqual(event.organization_id, self.org.id)

    def test_beat_schedule_registers_reminder_task(self):
        entry = celery_app.conf.beat_schedule["check-task-due-reminders"]
        self.assertEqual(entry["task"], "check_task_due_reminders")
        self.assertEqual(entry["schedule"], 900.0)
