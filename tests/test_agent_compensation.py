"""补偿：失败后反向补偿可补偿写步骤，不可补偿步骤有明确审计状态。"""

import json
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.agent import AgentAuditEvent, AgentRun, ToolCallLog
from app.models.task import Task
from app.models.user import User
from app.services.agent_compensation import run_compensation


class CompensationTests(unittest.TestCase):
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
        self.run = AgentRun(user_id=self.user.id, goal="g", status="error")
        self.db.add(self.run)
        self.db.commit()
        self.db.refresh(self.run)

    def tearDown(self):
        self.db.close()

    def _success_log(self, tool_name, observation_data, step):
        log = ToolCallLog(
            agent_run_id=self.run.id, step=step, tool_name=tool_name,
            status="success", observation=json.dumps({"success": True, "data": observation_data}, ensure_ascii=False),
        )
        self.db.add(log)
        return log

    def test_compensates_compensable_write_in_reverse_order(self):
        task = Task(user_id=self.user.id, title="t", status="todo")
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        # 两个写步骤（同一工具），反向补偿
        self._success_log("task_create_tool", {"task": {"id": task.id}}, 1)
        task2 = Task(user_id=self.user.id, title="t2", status="todo")
        self.db.add(task2)
        self.db.commit()
        self.db.refresh(task2)
        self._success_log("task_create_tool", {"task": {"id": task2.id}}, 2)
        self.db.commit()

        result = run_compensation(self.db, self.run)
        self.db.refresh(task)
        self.db.refresh(task2)
        self.assertEqual(task.status, "cancelled")
        self.assertEqual(task2.status, "cancelled")
        self.assertEqual(result["compensation_status"], "completed")
        # 反向顺序：step 2 先于 step 1
        steps = [item["step"] for item in result["records"]]
        self.assertEqual(steps, [2, 1])

    def test_not_compensable_write_is_audited(self):
        # 自定义写工具：未注册补偿器 → 明确记录 not_compensable
        from app.mcp.tool_contract import ToolContract

        class FakeWriteTool:
            name = "fake_write_tool"
            contract = ToolContract(name="fake_write_tool", read_only=False, requires_approval=True)

        with patch.dict("app.mcp.registry._TOOL_INSTANCES", {"fake_write_tool": FakeWriteTool()}):
            self._success_log("fake_write_tool", {"ok": True}, 1)
            self.db.commit()
            result = run_compensation(self.db, self.run)
        self.assertEqual(result["records"][0]["compensation_status"], "not_compensable")
        audit = self.db.query(AgentAuditEvent).filter(AgentAuditEvent.event_type == "compensation").all()
        self.assertEqual(audit[0].status, "not_compensable")

    def test_read_only_steps_not_compensated(self):
        self._success_log("document_search_tool", {"chunks": []}, 1)
        self.db.commit()
        result = run_compensation(self.db, self.run)
        self.assertEqual(result["compensation_status"], "none")
        self.assertEqual(result["records"], [])


if __name__ == "__main__":
    unittest.main()
