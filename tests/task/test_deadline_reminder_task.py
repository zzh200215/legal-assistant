"""Task 层：check_legal_deadline_reminders_task 期限提醒窗口与幂等去重。

覆盖 app/tasks/legal_tasks.py::check_legal_deadline_reminders_task：
- 提醒窗口：remind_at = deadline_at - offset_days，已到期（<= now）才触发；
- 幂等去重：同 deadline + offset 只产生一条通知（dedupe_key=deadline:{id}:offset:{n}）；
- 状态过滤：仅 active 期限参与扫描；
- 时区：aware/naive deadline_at 统一按 naive 比较（与 utc_now 一致）。

调用方式：直接调用任务函数本体（.run / __wrapped__），不依赖 Celery broker。
"""

import json
import unittest
from datetime import UTC, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.core.time import utc_now
from app.models.legal_notifications import LegalNotificationEvent
from app.models.legal_portal import LegalDeadline
from app.tasks.legal_tasks import check_legal_deadline_reminders_task


def _task_fn():
    # Celery Task.run = 任务函数本体（不经 broker/锁/心跳装饰链），
    # 锁语义已由 test_distributed_lock 覆盖，此处直调业务逻辑。
    return check_legal_deadline_reminders_task.run


class DeadlineReminderTaskTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        self.db = self.Session()
        self._session_patch = patch("app.tasks.legal_tasks.SessionLocal", self.Session)
        self._session_patch.start()
        self._heartbeat_patch = patch("app.tasks.legal_tasks._record_beat_heartbeat")
        self._heartbeat_patch.start()
        # 确定性阻断 redis（beat 锁 fail-open 放行；不依赖本机是否运行 redis）
        self._redis_patch = patch(
            "app.tasks.runtime.redis.from_url",
            side_effect=RuntimeError("redis unavailable in unit tests"),
        )
        self._redis_patch.start()

    def tearDown(self):
        self._redis_patch.stop()
        self._heartbeat_patch.stop()
        self._session_patch.stop()
        self.db.close()

    def _add_deadline(self, *, deadline_at, status="active", offsets="[7,3,1]", deadline_id=None):
        dl = LegalDeadline(
            id=deadline_id,
            organization_id=1,
            case_id=10,
            deadline_type="hearing",
            deadline_at=deadline_at,
            owner_id=1,
            status=status,
            reminder_offsets_json=offsets,
            created_by=1,
        )
        self.db.add(dl)
        self.db.commit()
        self.db.refresh(dl)
        return dl

    def _reminder_count(self) -> int:
        return self.db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.event_type == "deadline_reminder"
        ).count()

    def test_due_offset_triggers_reminder(self):
        """deadline 距今 5 天：仅 offset=7 的 remind_at 已到期 → 恰好 1 条提醒。"""
        now = utc_now()
        dl = self._add_deadline(deadline_at=now + timedelta(days=5))
        result = _task_fn()()
        self.assertEqual(result, {"created_reminders": 1})
        events = self.db.query(LegalNotificationEvent).all()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].body, f"deadline:{dl.id}:offset:7")
        self.assertEqual(events[0].channel, "site")
        self.assertEqual(events[0].status, "pending")
        self.assertEqual(events[0].reference_type, "deadline")
        self.assertEqual(events[0].reference_id, dl.id)

    def test_rerun_is_idempotent(self):
        """同一 deadline 重复扫描不产生重复提醒。"""
        now = utc_now()
        self._add_deadline(deadline_at=now + timedelta(days=5))
        self.assertEqual(_task_fn()()["created_reminders"], 1)
        self.assertEqual(_task_fn()()["created_reminders"], 0)
        self.assertEqual(self._reminder_count(), 1)

    def test_multiple_due_offsets_each_create_distinct_reminder(self):
        """deadline 距今 3 天：offset 7 与 3 均已到期 → 2 条，dedupe_key 各自独立。"""
        now = utc_now()
        dl = self._add_deadline(deadline_at=now + timedelta(days=3))
        self.assertEqual(_task_fn()()["created_reminders"], 2)
        bodies = {e.body for e in self.db.query(LegalNotificationEvent).all()}
        self.assertEqual(bodies, {f"deadline:{dl.id}:offset:7", f"deadline:{dl.id}:offset:3"})

    def test_future_deadline_creates_no_reminder(self):
        """全部提醒点都在未来（offset 最大 7 天 < 30 天）→ 不触发。"""
        now = utc_now()
        self._add_deadline(deadline_at=now + timedelta(days=30))
        self.assertEqual(_task_fn()()["created_reminders"], 0)
        self.assertEqual(self._reminder_count(), 0)

    def test_non_active_deadline_skipped(self):
        """completed / cancelled 状态不参与扫描。"""
        now = utc_now()
        self._add_deadline(deadline_at=now - timedelta(days=1), status="completed")
        self._add_deadline(deadline_at=now - timedelta(days=1), status="cancelled")
        self.assertEqual(_task_fn()()["created_reminders"], 0)

    def test_aware_deadline_at_triggers_same_as_naive(self):
        """aware（带时区）与 naive deadline_at 都按 naive 与 now 比较，行为一致。"""
        now = utc_now()
        self._add_deadline(deadline_at=now + timedelta(days=5))
        self._add_deadline(
            deadline_at=now.replace(tzinfo=UTC) + timedelta(days=5),
            deadline_id=999,
        )
        self.assertEqual(_task_fn()()["created_reminders"], 2)

    def test_custom_offsets_honored(self):
        """reminder_offsets_json 自定义偏移生效（如 [1] 且已到期）。"""
        now = utc_now()
        self._add_deadline(deadline_at=now + timedelta(days=1), offsets=json.dumps([1]))
        # offset=1 → remind_at=now，已到期 → 触发
        self.assertEqual(_task_fn()()["created_reminders"], 1)


if __name__ == "__main__":
    unittest.main()
