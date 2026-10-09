"""工作流 Outbox 投递、失败回退和租约回收测试。"""

import unittest
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.core.time import utc_now
from app.models.workflow import WorkflowEvent, WorkflowOutboxEvent
from app.services.workflows.workflow_service import workflow_service


class FakePublisher:
    def __init__(self, *, error=None):
        self.error = error
        self.messages = []
        self.closed = False

    def publish(self, channel, payload):
        if self.error:
            raise self.error
        self.messages.append((channel, payload))

    def close(self):
        self.closed = True


class WorkflowOutboxTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.session_factory()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _create_event(self):
        run = workflow_service.start(
            self.db, task_id="outbox-task", workflow_type="document_parse",
            organization_id=9, case_id=None,
        )
        outbox = self.db.query(WorkflowOutboxEvent).one()
        event = self.db.query(WorkflowEvent).one()
        return run, event, outbox

    def _read_outbox(self, outbox_id):
        db = self.session_factory()
        try:
            return db.get(WorkflowOutboxEvent, outbox_id)
        finally:
            db.close()

    def test_dispatch_publishes_and_marks_delivered(self):
        _, event, outbox = self._create_event()
        outbox_id = outbox.id
        event_id = event.id
        publisher = FakePublisher()
        with patch("app.tasks.workflow_tasks.SessionLocal", return_value=self.db), patch(
            "app.tasks.workflow_tasks.redis.Redis.from_url", return_value=publisher,
        ):
            from app.tasks.workflow_tasks import dispatch_workflow_outbox_task

            result = dispatch_workflow_outbox_task.run()

        self.assertEqual(result, {"delivered": 1, "retried": 0, "failed": 0})
        self.assertEqual(len(publisher.messages), 1)
        self.assertEqual(publisher.messages[0][0], "legal.workflow.events")
        stored = self._read_outbox(outbox_id)
        self.assertEqual(stored.status, "delivered")
        self.assertEqual(stored.workflow_event_id, event_id)
        self.assertTrue(publisher.closed)

    def test_publish_failure_releases_event_with_backoff(self):
        _, _, outbox = self._create_event()
        outbox_id = outbox.id
        publisher = FakePublisher(error=RuntimeError("redis unavailable"))
        with patch("app.tasks.workflow_tasks.SessionLocal", return_value=self.db), patch(
            "app.tasks.workflow_tasks.redis.Redis.from_url", return_value=publisher,
        ):
            from app.tasks.workflow_tasks import dispatch_workflow_outbox_task

            result = dispatch_workflow_outbox_task.run()

        self.assertEqual(result, {"delivered": 0, "retried": 1, "failed": 0})
        stored = self._read_outbox(outbox_id)
        self.assertEqual(stored.status, "pending")
        self.assertEqual(stored.attempts, 1)
        self.assertEqual(stored.claim_owner, None)
        self.assertIn("redis unavailable", stored.last_error)
        self.assertGreater(stored.available_at, utc_now())

    def test_missing_event_is_marked_failed_instead_of_stuck_sending(self):
        outbox = WorkflowOutboxEvent(workflow_event_id=999, event_type="workflow.started", status="pending")
        self.db.add(outbox)
        self.db.commit()
        outbox_id = outbox.id
        with patch("app.tasks.workflow_tasks.SessionLocal", return_value=self.db), patch(
            "app.tasks.workflow_tasks.redis.Redis.from_url", return_value=FakePublisher(),
        ):
            from app.tasks.workflow_tasks import dispatch_workflow_outbox_task

            result = dispatch_workflow_outbox_task.run()

        self.assertEqual(result, {"delivered": 0, "retried": 0, "failed": 1})
        stored = self._read_outbox(outbox_id)
        self.assertEqual(stored.status, "failed")
        self.assertIn("missing", stored.last_error)

    def test_stale_claim_is_recovered(self):
        _, _, outbox = self._create_event()
        outbox_id = outbox.id
        workflow_service.claim_outbox(self.db, owner="dead-worker")
        outbox.claimed_at = utc_now() - timedelta(minutes=11)
        self.db.commit()
        with patch("app.tasks.workflow_tasks.SessionLocal", return_value=self.db):
            from app.tasks.workflow_tasks import recover_stale_workflow_outbox_task

            result = recover_stale_workflow_outbox_task.run()

        self.assertEqual(result, {"recovered": 1})
        stored = self._read_outbox(outbox_id)
        self.assertEqual(stored.status, "pending")
        self.assertIsNone(stored.claim_owner)


if __name__ == "__main__":
    unittest.main()
