import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.legal import LegalCase
from app.models.workflow import WorkflowEvent, WorkflowOutboxEvent, WorkflowRun
from app.services.legal.matter_service import matter_service
from app.services.workflows.workflow_service import WorkflowCancelled, workflow_service


class MatterAndWorkflowServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.db.add(LegalCase(
            id=7, organization_id=11, user_id=3, title="劳动争议", case_type="labor_dispute",
            status="in_progress", is_strict_mode=0,
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    @patch("app.services.legal.matter_service.verify_case_access")
    def test_matter_activity_is_scoped_and_serialized(self, verify):
        verify.return_value = {"case": self.db.get(LegalCase, 7)}
        matter_service.record_activity(
            self.db, case_id=7, organization_id=11, actor_id=3,
            event_type="matter.created", title="创建案件", summary="劳动争议",
        )
        self.db.commit()
        rows = matter_service.activity(self.db, case_id=7, user_id=3)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_type"], "matter.created")
        self.assertGreaterEqual(verify.call_count, 2)

    def test_workflow_start_transition_creates_events_and_outbox(self):
        run = workflow_service.start(
            self.db, task_id="celery-1", workflow_type="parse_document",
            organization_id=11, user_id=3, case_id=7, business_key="document:9",
        )
        self.assertEqual(run.status, "running")
        self.assertEqual(self.db.query(WorkflowEvent).count(), 1)
        self.assertEqual(self.db.query(WorkflowOutboxEvent).count(), 1)

        run = workflow_service.transition(
            self.db, task_id="celery-1", status="succeeded", step="completed", progress=100,
        )
        self.assertEqual(run.status, "succeeded")
        self.assertEqual(self.db.query(WorkflowEvent).count(), 2)
        self.assertEqual(self.db.query(WorkflowOutboxEvent).count(), 2)

    def test_workflow_retry_reuses_task_id_and_preserves_history(self):
        first = workflow_service.start(
            self.db, task_id="celery-2", workflow_type="document_index", organization_id=11,
        )
        second = workflow_service.start(
            self.db, task_id="celery-2", workflow_type="document_index", organization_id=11,
        )
        self.assertEqual(first.id, second.id)
        self.assertEqual(second.attempt, 2)
        self.assertEqual(self.db.query(WorkflowEvent).count(), 2)

    def test_outbox_claim_is_idempotently_owned(self):
        workflow_service.start(self.db, task_id="celery-3", workflow_type="analyze_document")
        rows = workflow_service.claim_outbox(self.db, owner="worker-a")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, "sending")
        workflow_service.mark_outbox_delivered(self.db, event_id=rows[0].id, owner="worker-b")
        self.assertEqual(self.db.query(WorkflowOutboxEvent).first().status, "sending")
        workflow_service.mark_outbox_delivered(self.db, event_id=rows[0].id, owner="worker-a")
        self.assertEqual(self.db.query(WorkflowOutboxEvent).first().status, "delivered")

    def test_queued_workflow_cancel_is_terminal_and_records_event(self):
        row = WorkflowRun(
            task_id="queued-cancel", workflow_type="document_index", organization_id=11,
            user_id=3, case_id=7, status="queued", current_step="queued",
        )
        self.db.add(row)
        self.db.commit()

        cancelled = workflow_service.request_cancel(
            self.db, workflow_id=row.id, organization_id=11, actor_id=3, reason="user_cancelled",
        )

        self.assertEqual(cancelled.status, "cancelled")
        self.assertIsNotNone(cancelled.cancel_requested_at)
        event = self.db.query(WorkflowEvent).filter(WorkflowEvent.workflow_run_id == row.id).one()
        self.assertEqual(event.event_type, "workflow.cancel_requested")
        self.assertEqual(event.to_status, "cancelled")

        rerun = workflow_service.start(
            self.db, task_id="queued-cancel", workflow_type="document_index", organization_id=11,
        )
        self.assertEqual(rerun.status, "cancelled")
        self.assertEqual(rerun.attempt, 0)

    def test_running_workflow_cancel_is_cooperative_and_worker_finishes_it(self):
        row = workflow_service.start(
            self.db, task_id="running-cancel", workflow_type="analyze_document", organization_id=11,
        )
        requested = workflow_service.request_cancel(
            self.db, workflow_id=row.id, organization_id=11, actor_id=3, reason="不再需要",
        )
        self.assertEqual(requested.status, "cancelling")
        repeated = workflow_service.request_cancel(
            self.db, workflow_id=row.id, organization_id=11, actor_id=3, reason="重复点击",
        )
        self.assertEqual(repeated.status, "cancelling")
        self.assertEqual(
            self.db.query(WorkflowEvent).filter(WorkflowEvent.workflow_run_id == row.id).count(), 2,
        )

        with self.assertRaises(WorkflowCancelled):
            workflow_service.ensure_not_cancelled(self.db, task_id="running-cancel")

        final = self.db.get(WorkflowRun, row.id)
        self.assertEqual(final.status, "cancelled")
        self.assertEqual(final.error_code, WorkflowCancelled.error_code)
        event_types = [event.event_type for event in workflow_service.events(self.db, workflow_id=row.id)]
        self.assertIn("workflow.cancel_requested", event_types)
        self.assertIn("workflow.cancelled", event_types)

    def test_cancelled_workflow_cannot_be_overwritten_by_success_transition(self):
        row = workflow_service.start(
            self.db, task_id="success-after-cancel", workflow_type="document_chunk", organization_id=11,
        )
        workflow_service.request_cancel(self.db, workflow_id=row.id, organization_id=11)
        final = workflow_service.transition(
            self.db, task_id="success-after-cancel", status="succeeded", step="completed", progress=100,
        )
        self.assertEqual(final.status, "cancelled")
        self.assertEqual(final.error_code, WorkflowCancelled.error_code)


if __name__ == "__main__":
    unittest.main()
