"""工作流运行 API 的组织、案件和取消契约测试。"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import create_access_token, hash_password
from app.core.database import Base, get_db
from app.main import app
from app.models.legal import LegalCase
from app.models.document import Document
from app.models.org import Organization, OrganizationMember
from app.models.user import User, UserStatus
from app.models.workflow import WorkflowEvent, WorkflowRun
from app.services.workflows.workflow_service import workflow_service


class WorkflowApiTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        self.db = self.session_factory()

        self.org_a = Organization(name="律所A", code="workflow_a")
        self.org_b = Organization(name="律所B", code="workflow_b")
        self.db.add_all([self.org_a, self.org_b])
        self.db.flush()
        self.user_a = User(
            username="workflow-a", email="workflow-a@example.com", hashed_password=hash_password("pw"),
            organization_id=self.org_a.id, status=UserStatus.active.value,
        )
        self.admin_a = User(
            username="workflow-admin-a", email="workflow-admin-a@example.com", hashed_password=hash_password("pw"),
            organization_id=self.org_a.id, role="admin", status=UserStatus.active.value,
        )
        self.user_b = User(
            username="workflow-b", email="workflow-b@example.com", hashed_password=hash_password("pw"),
            organization_id=self.org_b.id, status=UserStatus.active.value,
        )
        self.orgless_user = User(
            username="workflow-none", email="workflow-none@example.com", hashed_password=hash_password("pw"),
            status=UserStatus.active.value,
        )
        self.db.add_all([self.user_a, self.admin_a, self.user_b, self.orgless_user])
        self.db.flush()
        self.db.add_all([
            OrganizationMember(organization_id=self.org_a.id, user_id=self.user_a.id, legal_role="editor"),
            OrganizationMember(organization_id=self.org_a.id, user_id=self.admin_a.id, legal_role="admin"),
            OrganizationMember(organization_id=self.org_b.id, user_id=self.user_b.id, legal_role="editor"),
        ])
        self.case_a = LegalCase(
            organization_id=self.org_a.id, user_id=self.user_a.id,
            title="案件A", case_type="labor_dispute", is_strict_mode=0,
        )
        self.case_b = LegalCase(
            organization_id=self.org_b.id, user_id=self.user_b.id,
            title="案件B", case_type="contract", is_strict_mode=0,
        )
        self.db.add_all([self.case_a, self.case_b])
        self.db.commit()
        for item in (self.org_a, self.org_b, self.user_a, self.admin_a, self.user_b, self.orgless_user, self.case_a, self.case_b):
            self.db.refresh(item)

        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)
        self.headers_a = {"Authorization": f"Bearer {create_access_token({'sub': self.user_a.id})}"}
        self.headers_admin = {"Authorization": f"Bearer {create_access_token({'sub': self.admin_a.id})}"}
        self.headers_none = {"Authorization": f"Bearer {create_access_token({'sub': self.orgless_user.id})}"}

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def _run(self, *, task_id, organization_id, case_id=None, status="running"):
        run = WorkflowRun(
            task_id=task_id, workflow_type="document_parse", organization_id=organization_id,
            user_id=self.user_a.id, case_id=case_id, status=status, current_step="started", progress=20,
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def test_list_is_scoped_by_organization_and_case_access(self):
        own = self._run(task_id="api-own", organization_id=self.org_a.id, case_id=self.case_a.id)
        foreign = self._run(task_id="api-foreign", organization_id=self.org_b.id, case_id=self.case_b.id)

        response = self.client.get("/api/tasks/workflows", headers=self.headers_a)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([item["id"] for item in response.json()["data"]], [own.id])

        response = self.client.get(
            f"/api/tasks/workflows?case_id={self.case_a.id}", headers=self.headers_a,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json()["data"]], [own.id])

        response = self.client.get(
            f"/api/tasks/workflows?case_id={self.case_b.id}", headers=self.headers_a,
        )
        self.assertEqual(response.status_code, 404)

    def test_get_hides_foreign_and_orgless_workflows(self):
        foreign = self._run(task_id="api-hidden", organization_id=self.org_b.id, case_id=self.case_b.id)
        response = self.client.get(f"/api/tasks/workflows/{foreign.id}", headers=self.headers_a)
        self.assertEqual(response.status_code, 404)

        own = self._run(task_id="api-orgless-check", organization_id=self.org_a.id, case_id=self.case_a.id)
        response = self.client.get(
            f"/api/tasks/workflows/{own.id}", headers=self.headers_none,
        )
        self.assertEqual(response.status_code, 404)

    def test_cancel_is_idempotent_and_does_not_duplicate_event(self):
        queued = self._run(
            task_id="api-queued-cancel", organization_id=self.org_a.id,
            case_id=self.case_a.id, status="queued",
        )

        first = self.client.post(f"/api/tasks/workflows/{queued.id}/cancel", headers=self.headers_a)
        second = self.client.post(f"/api/tasks/workflows/{queued.id}/cancel", headers=self.headers_a)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["data"]["status"], "cancelled")
        self.assertEqual(second.json()["data"]["status"], "cancelled")
        self.assertEqual(
            self.db.query(WorkflowEvent).filter(WorkflowEvent.workflow_run_id == queued.id).count(), 1,
        )

    def test_successful_workflow_cannot_be_cancelled(self):
        run = workflow_service.start(
            self.db, task_id="api-success", workflow_type="document_parse",
            organization_id=self.org_a.id, user_id=self.user_a.id, case_id=self.case_a.id,
        )
        workflow_service.transition(self.db, task_id=run.task_id, status="succeeded", progress=100)
        response = self.client.post(f"/api/tasks/workflows/{run.id}/cancel", headers=self.headers_a)
        self.assertEqual(response.status_code, 409)

    def test_failed_document_workflow_can_be_retried_and_keeps_history(self):
        document = Document(
            user_id=self.user_a.id, organization_id=self.org_a.id,
            title="合同", file_type="pdf", status="failed",
        )
        self.db.add(document)
        self.db.commit()
        self.db.refresh(document)
        run = self._run(
            task_id="api-retry-source", organization_id=self.org_a.id,
            case_id=self.case_a.id, status="failed",
        )
        run.business_key = f"document:{document.id}"
        run.workflow_type = "parse_document"
        self.db.commit()

        with patch("app.tasks.parse_document_task.delay", return_value=SimpleNamespace(id="api-retry-task")):
            response = self.client.post(f"/api/tasks/workflows/{run.id}/retry", headers=self.headers_a)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"], {
            "retry_of": run.id, "task_id": "api-retry-task", "status": "queued",
        })
        events = self.db.query(WorkflowEvent).filter(WorkflowEvent.workflow_run_id == run.id).all()
        self.assertEqual([event.event_type for event in events], ["workflow.retry_requested"])
        self.assertEqual(run.status, "failed")
        duplicate = self.client.post(f"/api/tasks/workflows/{run.id}/retry", headers=self.headers_a)
        self.assertEqual(duplicate.status_code, 409)

    def test_retry_rejects_non_failed_or_unsupported_workflow(self):
        active = self._run(
            task_id="api-retry-active", organization_id=self.org_a.id,
            case_id=self.case_a.id, status="running",
        )
        response = self.client.post(f"/api/tasks/workflows/{active.id}/retry", headers=self.headers_a)
        self.assertEqual(response.status_code, 409)

        unsupported = self._run(
            task_id="api-retry-unsupported", organization_id=self.org_a.id,
            case_id=self.case_a.id, status="failed",
        )
        unsupported.workflow_type = "unknown_task"
        self.db.commit()
        response = self.client.post(f"/api/tasks/workflows/{unsupported.id}/retry", headers=self.headers_a)
        self.assertEqual(response.status_code, 409)

    def test_admin_workflow_overview_is_org_scoped_and_non_admin_is_denied(self):
        own = workflow_service.start(
            self.db, task_id="api-overview-own", workflow_type="document_parse",
            organization_id=self.org_a.id, user_id=self.admin_a.id, case_id=self.case_a.id,
        )
        self._run(task_id="api-overview-foreign", organization_id=self.org_b.id, case_id=self.case_b.id)

        response = self.client.get("/api/analytics/workflows/overview", headers=self.headers_admin)
        self.assertEqual(response.status_code, 200)
        body = response.json()["data"]
        self.assertEqual(body["runs"]["total"], 1)
        self.assertEqual(body["runs"]["by_status"]["running"], 1)
        self.assertGreaterEqual(body["outbox"]["pending"], 1)
        self.assertEqual(body["organization_id"], self.org_a.id)
        self.assertEqual(own.organization_id, self.org_a.id)

        response = self.client.get("/api/analytics/workflows/overview", headers=self.headers_a)
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
