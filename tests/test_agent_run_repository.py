"""RunStateRepository：持久化 run/snapshot/工具日志，按 run_id 恢复，已成功步骤不重复。"""

import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.agent import AgentRun, ToolCallLog
from app.models.user import User
from app.services.agent_run_repository import run_state_repository
from app.services.agent_run_state import AgentPlan, AgentRunState


class RunStateRepositoryTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:", future=True,
            connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        self.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        self.db = self.SessionLocal()
        self.user = User(username="u", email="u@e.com", hashed_password="h")
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

    def tearDown(self):
        self.db.close()

    def test_create_and_fetch_run_with_trace_and_org(self):
        run = run_state_repository.create_run(
            self.db, goal="g", user_id=self.user.id, trace_id="tr-1", organization_id=9,
        )
        self.assertEqual(run.status, "running")
        self.assertEqual(run.trace_id, "tr-1")
        self.assertEqual(run.organization_id, 9)
        fetched = run_state_repository.get_run(self.db, run.id, user_id=self.user.id)
        self.assertEqual(fetched.id, run.id)

    def test_snapshot_persists_typed_state_and_restores(self):
        run = run_state_repository.create_run(self.db, goal="g", user_id=self.user.id, trace_id="tr-1")
        plan = AgentPlan(intent="审查合同", workers=["legal_compliance_agent"], dependencies=[],
                         risk_level="medium", expected_artifacts=["document"],
                         execution_mode="sequential", rationale="r", plan_source="llm")
        state = AgentRunState(run_id=run.id, user_id=self.user.id, trace_id="tr-1", plan=plan, step=2)
        snapshot = {"node": "decide", "worker_plan": ["legal_compliance_agent"], "step": 2}
        run_state_repository.save_workflow_state(self.db, run, snapshot=snapshot, state=state, node="decide", status="running")

        refreshed = run_state_repository.get_run(self.db, run.id)
        raw = json.loads(refreshed.workflow_state)
        self.assertEqual(raw["node"], "decide")
        self.assertIn("model", raw)
        restored = run_state_repository.load_state_model(refreshed)
        self.assertEqual(restored.plan.workers, ["legal_compliance_agent"])
        self.assertEqual(restored.step, 2)

    def test_append_and_fetch_logs_scoped(self):
        run = run_state_repository.create_run(self.db, goal="g", user_id=self.user.id)
        run_state_repository.append_log(
            self.db, agent_run_id=run.id, step=1, decision={"action_type": "tool_call"},
            raw_decision="{}", tool_name="document_search_tool", input_params={"db": "x", "q": "a"},
            observation="{}", output_result="", status="success", error=None, duration_ms=5,
        )
        logs = run_state_repository.get_run_logs(self.db, run.id, user_id=self.user.id)
        self.assertEqual(len(logs), 1)
        # db 不得进入日志输入参数
        self.assertNotIn('"db"', logs[0].input_params)

    def test_update_log_and_save_run(self):
        run = run_state_repository.create_run(self.db, goal="g", user_id=self.user.id)
        log = run_state_repository.append_log(
            self.db, agent_run_id=run.id, step=1, decision={}, raw_decision="",
            tool_name="finish", input_params={}, observation="", output_result="", status="success", error=None, duration_ms=0,
        )
        run_state_repository.update_log(self.db, log, status="approved")
        self.assertEqual(run_state_repository.get_run_logs(self.db, run.id)[0].status, "approved")
        run_state_repository.save_run(self.db, run, status="completed", total_steps=1)
        self.assertEqual(run_state_repository.get_run(self.db, run.id).status, "completed")


if __name__ == "__main__":
    unittest.main()
