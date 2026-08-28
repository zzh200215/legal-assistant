"""ToolExecutor 统一执行链路：审批闸、幂等、重试、取消、超时、权限不可绕过。

覆盖验收 2/3/4/5/6/8：读授权执行、写无审批阻断、审批后改参重新审批、
直调节点/fallback 无法绕过权限、同幂等键不重复副作用、可重试/不可重试策略、
取消阻断、工具超时。
"""

import asyncio
import time
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.config import get_settings
from app.core.database import Base
from app.mcp.tool_contract import ToolContract
from app.models.agent import AgentAuditEvent, AgentRun
from app.models.idempotency import IdempotencyKey
from app.models.user import User
from app.services.agent.agent_approval_service import agent_approval_service
from app.services.agent.agent_service import AgentService
from app.tools.base import BaseAgentTool, tool_error, tool_success


def _make_tool(tool_name, contract_kwargs, handler=None, calls=None):
    class FakeTool(BaseAgentTool):
        name = tool_name
        contract = ToolContract(name=tool_name, **contract_kwargs)
        parameters = {"type": "object", "properties": {}, "required": []}

        async def run(self, **kwargs):
            if calls is not None:
                calls.append(kwargs)
            if handler is not None:
                result = handler(**kwargs)
                if asyncio.iscoroutine(result):
                    return await result
                return result
            return tool_success("ok", {"echo": kwargs})

    return FakeTool()


class ExecutorFixture(unittest.IsolatedAsyncioTestCase):
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
        self.service = AgentService()

    def tearDown(self):
        self.db.close()

    def _run_row(self, status="running"):
        run = AgentRun(user_id=self.user.id, goal="g", status=status)
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def _patch_tools(self, tools):
        return patch.dict("app.mcp.registry._TOOL_INSTANCES", tools, clear=True)


class ExecutorApprovalAndPermissionTests(ExecutorFixture):
    async def test_read_tool_executes_after_auth(self):
        calls = []
        read = _make_tool("document_search_tool", {"read_only": True, "requires_approval": False}, calls=calls)
        with self._patch_tools({"document_search_tool": read}):
            from app.mcp.executor import tool_executor

            result, _ = await tool_executor.execute(
                "document_search_tool", {"q": "x"}, agent_type="knowledge_agent",
                user_id=self.user.id, db=self.db,
            )
        self.assertTrue(result["success"])
        self.assertEqual(len(calls), 1)

    async def test_write_tool_blocked_without_approval(self):
        write = _make_tool("task_create_tool", {"read_only": False, "requires_approval": True})
        with self._patch_tools({"task_create_tool": write}):
            from app.mcp.executor import tool_executor

            result, _ = await tool_executor.execute(
                "task_create_tool", {"title": "t"}, agent_type="workflow_agent",
                user_id=self.user.id, db=self.db,
            )
        self.assertEqual(result["mcp_error_code"], "MCP_APPROVAL_REQUIRED")
        approval = agent_approval_service.list_requests(db=self.db, user_id=self.user.id)[0]
        self.assertEqual(approval.status, "pending")
        self.assertIsNotNone(approval.param_digest)

    async def test_approval_param_change_requires_reapproval(self):
        """审批后参数变化 → require_executable 拒绝 → 必须重新审批。"""
        approval = agent_approval_service.create_request(
            db=self.db, user_id=self.user.id, tool_name="task_create_tool",
            input_params={"title": "A"}, agent_type="workflow_agent",
        )
        agent_approval_service.decide_request(
            db=self.db, approval_id=approval.id, user_id=self.user.id, approved=True,
        )
        # 参数未变 → 可执行
        agent_approval_service.require_executable(
            db=self.db, approval_id=approval.id, user_id=self.user.id, current_params={"title": "A"},
        )
        # 参数变化 → 抛 ApprovalStateError，不得执行
        from app.services.agent.agent_approval_service import ApprovalStateError

        with self.assertRaises(ApprovalStateError):
            agent_approval_service.require_executable(
                db=self.db, approval_id=approval.id, user_id=self.user.id, current_params={"title": "B"},
            )

    async def test_no_permission_bypass_via_direct_node(self):
        """直接调用 workflow node，越权工具仍被 ACL 拒绝。"""
        executed = []
        write = _make_tool(
            "task_create_tool", {"read_only": False, "requires_approval": True},
            handler=lambda **kw: tool_success("written", {}), calls=executed,
        )
        run = self._run_row()
        state = {
            "goal": "g", "user_id": self.user.id, "db": self.db, "session_id": None,
            "memory_context": "", "max_steps": 5, "event_callback": None,
            "agent_run": run, "run_started": 0, "master_agent": "supervisor_agent",
            "worker_agent": "workflow_agent", "supervisor_plan": {}, "task_contract": {},
            "current_tool_name": "task_create_tool", "current_safe_input": {"title": "t"},
            "current_worker_agent": "knowledge_agent",  # knowledge 无权调用写工具
            "step": 1, "step_started_at": 0, "current_decision": {"action_type": "tool_call"},
            "current_raw": "x", "evidence_scope_seen": False, "last_observation": "",
            "retry_count": 0, "messages": [],
        }
        with self._patch_tools({"task_create_tool": write}):
            await self.service._workflow_tool_call(state)
        self.assertEqual(executed, [])
        logs = self.service.get_run_logs(run.id, self.db, user_id=self.user.id)
        self.assertEqual(logs[-1].status, "error")
        self.assertIn("MCP_PERMISSION_DENIED", (logs[-1].observation or ""))

    async def test_fallback_engine_does_not_auto_approve_writes(self):
        """fallback 引擎执行写工具仍走审批闸（不自动批准）。"""
        write = _make_tool("task_create_tool", {"read_only": False, "requires_approval": True})
        with self._patch_tools({"task_create_tool": write}):
            from app.mcp.executor import tool_executor

            result, _ = await tool_executor.execute(
                "task_create_tool", {"title": "t"}, agent_type="workflow_agent",
                user_id=self.user.id, db=self.db,
            )
        self.assertEqual(result["mcp_error_code"], "MCP_APPROVAL_REQUIRED")


class ExecutorIdempotencyAndRetryTests(ExecutorFixture):
    async def test_same_idempotency_key_no_double_side_effect(self):
        executed = []
        write = _make_tool(
            "task_create_tool", {"read_only": False, "requires_approval": True, "idempotency_keyed": True},
            handler=lambda **kw: tool_success("written", {"task": {"id": 1}}), calls=executed,
        )
        run = self._run_row()
        with self._patch_tools({"task_create_tool": write}):
            from app.mcp.executor import tool_executor

            r1, _ = await tool_executor.execute(
                "task_create_tool", {"title": "t"}, agent_type="workflow_agent",
                user_id=self.user.id, db=self.db, agent_run_id=run.id, step_id=1, skip_approval=True,
            )
            r2, _ = await tool_executor.execute(
                "task_create_tool", {"title": "t"}, agent_type="workflow_agent",
                user_id=self.user.id, db=self.db, agent_run_id=run.id, step_id=1, skip_approval=True,
            )
        self.assertTrue(r1["success"])
        self.assertTrue(r2["success"])
        self.assertTrue(r2["data"].get("idempotent_replay"))
        self.assertEqual(len(executed), 1)  # 幂等重放不产生重复副作用
        row = (
            self.db.query(IdempotencyKey)
            .filter(IdempotencyKey.scope == "agent_tool")
            .first()
        )
        self.assertEqual(row.status, "completed")

    async def test_retryable_transient_retried_per_policy(self):
        attempts = []

        def flaky(**kw):
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError("temporary infrastructure error")
            return tool_success("ok", {})

        read = _make_tool(
            "document_search_tool",
            {"read_only": True, "requires_approval": False, "retryable": True, "max_retries": 2, "backoff_base_seconds": 0},
            handler=flaky,
        )
        # 契约声明 2 次重试，全局上限须放到 2 才可能用满（默认 1 = 只重试 1 次）。
        with self._patch_tools({"document_search_tool": read}), patch.object(
            get_settings(), "AGENT_TOOL_MAX_RETRIES", 2
        ):
            from app.mcp.executor import tool_executor

            result, _ = await tool_executor.execute(
                "document_search_tool", {"q": "x"}, agent_type="knowledge_agent",
                user_id=self.user.id, db=self.db,
            )
        # 2 次重试 + 1 次初试 = 3 次尝试；第 3 次成功
        self.assertEqual(len(attempts), 3)
        self.assertTrue(result["success"])

    async def test_non_retryable_write_not_blindly_retried(self):
        attempts = []
        write = _make_tool(
            "task_create_tool",
            {"read_only": False, "requires_approval": True, "retryable": False, "max_retries": 3, "safely_retryable": False},
            handler=lambda **kw: (attempts.append(1) or tool_error("boom", "boom")),
        )
        run = self._run_row()
        with self._patch_tools({"task_create_tool": write}):
            from app.mcp.executor import tool_executor

            result, _ = await tool_executor.execute(
                "task_create_tool", {"title": "t"}, agent_type="workflow_agent",
                user_id=self.user.id, db=self.db, agent_run_id=run.id, step_id=1, skip_approval=True,
            )
        self.assertFalse(result["success"])
        self.assertEqual(len(attempts), 1)  # 写工具绝不盲目重试

    async def test_cancel_check_blocks_execution(self):
        executed = []
        read = _make_tool("document_search_tool", {"read_only": True}, calls=executed)
        with self._patch_tools({"document_search_tool": read}):
            from app.mcp.executor import tool_executor

            result, _ = await tool_executor.execute(
                "document_search_tool", {"q": "x"}, agent_type="knowledge_agent",
                user_id=self.user.id, db=self.db, cancel_check=lambda: True,
            )
        self.assertEqual(result["mcp_error_code"], "AGENT_CANCELLED")
        self.assertEqual(executed, [])

    async def test_tool_timeout_returns_structured_timeout(self):
        async def never(**kw):
            await asyncio.sleep(5)

        slow = _make_tool("document_search_tool", {"read_only": True, "timeout_seconds": 1}, handler=never)
        with self._patch_tools({"document_search_tool": slow}):
            from app.mcp.executor import tool_executor

            result, _ = await tool_executor.execute(
                "document_search_tool", {"q": "x"}, agent_type="knowledge_agent",
                user_id=self.user.id, db=self.db,
            )
        self.assertEqual(result["mcp_error_code"], "AGENT_TOOL_TIMEOUT")


class ExecutorBudgetAndGlobalCapTests(ExecutorFixture):
    """全局开关（AGENT_TOOL_*）与契约的分工：只能收紧，不能放宽；预算约束整个重试循环。"""

    async def test_global_cap_zero_suppresses_contract_retries(self):
        attempts = []

        def always_transient(**kw):
            attempts.append(1)
            raise RuntimeError("temporary infrastructure error")

        read = _make_tool(
            "document_search_tool",
            {"read_only": True, "retryable": True, "max_retries": 3, "backoff_base_seconds": 0},
            handler=always_transient,
        )
        with self._patch_tools({"document_search_tool": read}), patch.object(
            get_settings(), "AGENT_TOOL_MAX_RETRIES", 0
        ):
            from app.mcp.executor import tool_executor

            result, _ = await tool_executor.execute(
                "document_search_tool", {"q": "x"}, agent_type="knowledge_agent",
                user_id=self.user.id, db=self.db,
            )
        self.assertFalse(result["success"])
        self.assertEqual(len(attempts), 1)  # 运维可全局止损：契约声明 3 次也被压到 0

    async def test_global_cap_cannot_escalate_non_retryable_tool(self):
        attempts = []
        write = _make_tool(
            "task_create_tool",
            {"read_only": False, "requires_approval": True, "retryable": False, "max_retries": 0},
            handler=lambda **kw: (attempts.append(1) or tool_error("boom", "boom")),
        )
        run = self._run_row()
        with self._patch_tools({"task_create_tool": write}), patch.object(
            get_settings(), "AGENT_TOOL_MAX_RETRIES", 5
        ):
            from app.mcp.executor import tool_executor

            result, _ = await tool_executor.execute(
                "task_create_tool", {"title": "t"}, agent_type="workflow_agent",
                user_id=self.user.id, db=self.db, agent_run_id=run.id, step_id=1, skip_approval=True,
            )
        self.assertFalse(result["success"])
        self.assertEqual(len(attempts), 1)  # 全局开关调高也不能让不可重试的写工具重试

    async def test_backoff_base_falls_back_to_settings(self):
        attempts = []

        def always_transient(**kw):
            attempts.append(1)
            raise RuntimeError("temporary infrastructure error")

        # 契约不声明 backoff_base_seconds → 用 AGENT_TOOL_BACKOFF_BASE_SECONDS。
        read = _make_tool(
            "document_search_tool",
            {"read_only": True, "retryable": True, "max_retries": 1},
            handler=always_transient,
        )
        sleeper = AsyncMock()
        with self._patch_tools({"document_search_tool": read}), patch.object(
            get_settings(), "AGENT_TOOL_BACKOFF_BASE_SECONDS", 7
        ), patch("app.mcp.executor.asyncio.sleep", sleeper):
            from app.mcp.executor import tool_executor

            await tool_executor.execute(
                "document_search_tool", {"q": "x"}, agent_type="knowledge_agent",
                user_id=self.user.id, db=self.db,
            )
        self.assertEqual(len(attempts), 2)
        sleeper.assert_awaited_once_with(7.0)  # backoff_base * attempt(=1)

    async def test_caller_budget_tightens_tool_timeout(self):
        async def never(**kw):
            await asyncio.sleep(30)

        # 契约允许跑 60s，但调用方只剩 0.2s → 以 0.2s 判超时。
        slow = _make_tool("document_search_tool", {"read_only": True, "timeout_seconds": 60}, handler=never)
        started = time.monotonic()
        with self._patch_tools({"document_search_tool": slow}):
            from app.mcp.executor import tool_executor

            result, _ = await tool_executor.execute(
                "document_search_tool", {"q": "x"}, agent_type="knowledge_agent",
                user_id=self.user.id, db=self.db, timeout_budget_seconds=0.2,
            )
        elapsed = time.monotonic() - started
        self.assertEqual(result["mcp_error_code"], "AGENT_TOOL_TIMEOUT")
        self.assertLess(elapsed, 5)
        self.assertLessEqual(result["data"]["timeout_seconds"], 0.2)

    async def test_retry_backoff_never_exceeds_caller_budget(self):
        attempts = []

        def always_transient(**kw):
            attempts.append(1)
            raise RuntimeError("temporary infrastructure error")

        read = _make_tool(
            "document_search_tool",
            {"read_only": True, "retryable": True, "max_retries": 1, "backoff_base_seconds": 30},
            handler=always_transient,
        )
        started = time.monotonic()
        with self._patch_tools({"document_search_tool": read}):
            from app.mcp.executor import tool_executor

            result, _ = await tool_executor.execute(
                "document_search_tool", {"q": "x"}, agent_type="knowledge_agent",
                user_id=self.user.id, db=self.db, timeout_budget_seconds=0.3,
            )
        elapsed = time.monotonic() - started
        self.assertFalse(result["success"])
        # 预算装不下「退避 30s + 再试一次」→ 不重试，也不睡
        self.assertEqual(len(attempts), 1)
        self.assertLess(elapsed, 5)

    async def test_no_budget_keeps_contract_timeout(self):
        """不传预算时行为不变：仍用契约声明的超时（回归保护）。"""
        captured = {}

        async def capture(**kwargs):
            captured.update(kwargs)
            return tool_success("ok", {})

        read = _make_tool("document_search_tool", {"read_only": True, "timeout_seconds": 11})
        with self._patch_tools({"document_search_tool": read}):
            from app.mcp.executor import tool_executor

            with patch.object(tool_executor, "_invoke_once", side_effect=capture) as invoke:
                await tool_executor.execute(
                    "document_search_tool", {"q": "x"}, agent_type="knowledge_agent",
                    user_id=self.user.id, db=self.db,
                )
        invoke.assert_awaited_once()
        self.assertEqual(captured["timeout_seconds"], 11.0)


class ExecutorAuditTests(ExecutorFixture):
    async def test_executor_emits_audit_events(self):
        read = _make_tool("document_search_tool", {"read_only": True})
        run = self._run_row()
        with self._patch_tools({"document_search_tool": read}):
            from app.mcp.executor import tool_executor

            await tool_executor.execute(
                "document_search_tool", {"q": "x"}, agent_type="knowledge_agent",
                user_id=self.user.id, db=self.db, agent_run_id=run.id, step_id=1, trace_id="tr",
            )
        events = self.db.query(AgentAuditEvent).filter(AgentAuditEvent.run_id == run.id).all()
        types = {e.event_type for e in events}
        self.assertIn("tool_executed", types)


if __name__ == "__main__":
    unittest.main()
