"""单步墙钟预算：AGENT_STEP_DEADLINE_SECONDS 与 run 剩余时间真正约束步内耗时。

此前这两个开关只是声明：AGENT_STEP_DEADLINE_SECONDS 没有任何代码读它，
AGENT_RUN_DEADLINE_SECONDS 只在步边界被检查一次，因此一次挂死的 LLM 调用或慢工具
能把整个 run 拖到截止时间之后很远。本测试锁定：

- ``_step_budget_seconds`` 取 min(单步上限剩余, run 剩余)，不为负，无约束时返回 None。
- decide 节点的 LLM 调用受预算约束，超时按既有 timeout 决策收敛到 partial。
- tool_call 把本步剩余预算传给执行器；审批恢复路径**不传**（步时钟已过期）。
"""

import asyncio
import time
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.config import get_settings
from app.core.database import Base
from app.core.time import utc_now
from app.models.agent import AgentRun
from app.models.user import User
from app.services.agent.agent_run_state import AgentRunState
from app.services.agent.agent_runtime import AgentRuntime
from app.services.agent.agent_service import AgentService
from app.tools.base import tool_success


class StepBudgetFixture(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:", future=True,
            connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        self.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        self.db = self.SessionLocal()
        self.user = User(username="tester", email="t@example.com", hashed_password="h")
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)
        self.run = AgentRun(user_id=self.user.id, goal="g", status="running")
        self.db.add(self.run)
        self.db.commit()
        self.db.refresh(self.run)
        self.service = AgentService()
        self.settings = get_settings()

    def tearDown(self):
        self.db.close()

    def _model(self, *, deadline_offset_seconds: float | None = None) -> AgentRunState:
        deadline = (
            (utc_now() + timedelta(seconds=deadline_offset_seconds)).isoformat()
            if deadline_offset_seconds is not None
            else None
        )
        return AgentRunState(run_id=self.run.id, user_id=self.user.id, run_deadline_at=deadline)

    def _runtime(self, model: AgentRunState | None = None) -> AgentRuntime:
        return AgentRuntime(db=self.db, agent_run=self.run, user_id=self.user.id, model=model)

    def _state(self, **overrides) -> dict:
        state = {
            "goal": "g", "user_id": self.user.id, "db": self.db, "session_id": None,
            "memory_context": "", "max_steps": 5, "event_callback": None,
            "agent_run": self.run, "run_started": time.time(),
            "master_agent": "supervisor_agent", "worker_agent": "knowledge_agent",
            "supervisor_plan": {}, "task_contract": {},
            "step": 0, "step_started_at": time.time(),
            "current_decision": None, "current_raw": "", "current_action_type": "",
            "current_tool_name": None, "current_safe_input": {},
            "current_worker_agent": "knowledge_agent",
            "evidence_scope_seen": False, "last_observation": "", "retry_count": 0,
            "messages": [{"role": "user", "content": "hi"}],
        }
        state.update(overrides)
        return state


class StepBudgetArithmeticTests(StepBudgetFixture):
    def test_budget_takes_min_of_step_limit_and_run_remaining(self):
        runtime = self._runtime(self._model(deadline_offset_seconds=3))
        budget = self.service._step_budget_seconds(runtime)
        self.assertIsNotNone(budget)
        # run 只剩 ~3s，远小于默认单步上限 120s
        self.assertLessEqual(budget, 3.0)
        self.assertGreater(budget, 1.0)

    def test_budget_deducts_step_elapsed_time(self):
        runtime = self._runtime()
        with patch.object(self.settings, "AGENT_STEP_DEADLINE_SECONDS", 120):
            budget = self.service._step_budget_seconds(
                runtime, step_started_at=time.time() - 100
            )
        self.assertIsNotNone(budget)
        self.assertLessEqual(budget, 20.0)
        self.assertGreater(budget, 15.0)

    def test_budget_never_negative(self):
        runtime = self._runtime(self._model(deadline_offset_seconds=-30))
        with patch.object(self.settings, "AGENT_STEP_DEADLINE_SECONDS", 120):
            budget = self.service._step_budget_seconds(
                runtime, step_started_at=time.time() - 9999
            )
        self.assertEqual(budget, 0.0)

    def test_budget_is_none_without_any_limit(self):
        runtime = self._runtime()
        with patch.object(self.settings, "AGENT_STEP_DEADLINE_SECONDS", 0):
            self.assertIsNone(self.service._step_budget_seconds(runtime))


class DecideStepDeadlineTests(StepBudgetFixture):
    async def test_slow_llm_call_converges_to_partial(self):
        async def hang(*args, **kwargs):
            await asyncio.sleep(30)
            return "never"

        state = self._state()
        started = time.monotonic()
        with patch.object(self.service, "_chat", side_effect=hang), patch.object(
            self.service, "_step_budget_seconds", return_value=0.05
        ):
            state[  # runtime 经 legacy state 键注入（直调节点用）
                "_model"
            ] = self._model()
            result = await self.service._workflow_decide(state)
        elapsed = time.monotonic() - started
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["current_raw"], "step_deadline_exceeded")
        self.assertEqual(result["current_decision"]["action_type"], "timeout")
        # 路由收敛到既有的 partial 终态，不新增终点
        self.assertEqual(self.service._workflow_route_decision(result), "partial")
        self.assertLess(elapsed, 5)

    async def test_normal_call_unaffected_when_budget_is_ample(self):
        state = self._state()
        state["_model"] = self._model(deadline_offset_seconds=600)
        with patch.object(
            self.service, "_chat", new=AsyncMock(return_value='{"action_type": "finish", "answer": "done"}')
        ):
            result = await self.service._workflow_decide(state)
        self.assertFalse(result.get("timed_out"))
        self.assertEqual(result["current_action_type"], "finish")

    async def test_partial_reports_which_deadline_fired(self):
        state = self._state(timed_out=True, current_raw="step_deadline_exceeded")
        state["_model"] = self._model()
        await self.service._workflow_partial(state)
        self.db.refresh(self.run)
        self.assertEqual(self.run.failure_reason, "step_timeout")

    async def test_partial_still_reports_run_timeout(self):
        state = self._state(timed_out=True, current_raw="run_deadline_exceeded")
        state["_model"] = self._model()
        await self.service._workflow_partial(state)
        self.db.refresh(self.run)
        self.assertEqual(self.run.failure_reason, "run_timeout")


class BudgetPassthroughTests(StepBudgetFixture):
    def _patched_executor(self):
        return patch.object(
            self.service,
            "_executor",
            AsyncMock(execute=AsyncMock(return_value=(tool_success("ok", {}), "{}"))),
        )

    async def test_tool_call_passes_remaining_step_budget(self):
        state = self._state(
            step=1,
            step_started_at=time.time() - 5,
            current_tool_name="document_search_tool",
            current_safe_input={"query": "x"},
            current_decision={"action_type": "tool_call"},
            current_raw="raw",
        )
        state["_model"] = self._model(deadline_offset_seconds=600)
        with self._patched_executor() as executor, patch.object(
            self.settings, "AGENT_STEP_DEADLINE_SECONDS", 120
        ):
            await self.service._workflow_tool_call(state)
        budget = executor.execute.await_args.kwargs["timeout_budget_seconds"]
        self.assertIsNotNone(budget)
        # 本步已耗 ~5s，剩余预算必须扣掉
        self.assertLess(budget, 120.0)
        self.assertGreater(budget, 100.0)

    async def test_approved_tool_execution_is_not_budgeted(self):
        """审批恢复可能发生在数小时后，state 里的步时钟已过期，不能按它算预算。"""
        state = self._state(
            step=1,
            step_started_at=time.time() - 7200,
            current_tool_name="task_create_tool",
            current_safe_input={"title": "t"},
            current_decision={"action_type": "tool_call"},
            current_raw="raw",
        )
        state["_model"] = self._model()
        approved = {
            "tool_name": "task_create_tool",
            "action_input": {"title": "t"},
            "agent_type": "workflow_agent",
            "approval_id": 0,
            "step": 1,
            "raw_decision": "raw",
        }
        with self._patched_executor() as executor:
            await self.service._workflow_execute_approved_tool(state, approved)
        self.assertIsNone(executor.execute.await_args.kwargs["timeout_budget_seconds"])


if __name__ == "__main__":
    unittest.main()
