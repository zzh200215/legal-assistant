import unittest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.services.agent.agent_approval_service import agent_approval_service
from app.models.agent import AgentRun, ToolCallLog
from app.models.user import User
from app.services.agent.agent_service import AgentService
from app.tools.base import BaseAgentTool, tool_success


class FakeTool(BaseAgentTool):
    def __init__(self, name, description, auto_context_fields=(), handler=None, parameters=None):
        self.name = name
        self.description = description
        self.auto_context_fields = auto_context_fields
        self.parameters = parameters or {"type": "object", "properties": {}, "required": []}
        self._handler = handler or (lambda **kwargs: tool_success("ok", kwargs))

    async def run(self, **kwargs):
        return self._handler(**kwargs)


class AgentServiceFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        self.db = TestingSessionLocal()
        self.user = User(
            username="tester",
            email="tester@example.com",
            hashed_password="secret",
        )
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)
        self.service = AgentService()
        self.supervisor_generate = patch(
            "app.services.agent.agent_service.llm_service.generate",
            new=AsyncMock(return_value="{}"),
        )
        self.supervisor_generate.start()

    def tearDown(self):
        self.supervisor_generate.stop()
        self.db.close()

    async def test_supervisor_plan_validates_model_output_and_falls_back_safely(self):
        valid_plan = json.dumps(
            {
                "intent": "审查合同后生成跟进任务",
                "workers": ["legal_compliance_agent", "workflow_agent"],
                "dependencies": [{"from": "legal_compliance_agent", "to": "workflow_agent"}],
                "risk_level": "medium",
                "expected_artifacts": ["document", "task"],
                "rationale": "先审查合同，再创建跟进任务。",
            }
        )
        with patch(
            "app.services.agent.agent_service.llm_service.generate",
            new=AsyncMock(return_value=valid_plan),
        ):
            plan = await self.service._plan_with_supervisor("审查合同并生成跟进任务", self.user.id)
        self.assertEqual(plan["plan_source"], "llm")
        self.assertEqual(plan["workers"], ["legal_compliance_agent", "workflow_agent"])
        self.assertEqual(plan["dependencies"][0]["to"], "workflow_agent")

        invalid_plan = json.dumps(
            {
                "workers": ["workflow_agent", "legal_compliance_agent"],
                "dependencies": [{"from": "legal_compliance_agent", "to": "workflow_agent"}],
                "risk_level": "urgent",
                "expected_artifacts": ["unknown"],
            }
        )
        with patch(
            "app.services.agent.agent_service.llm_service.generate",
            new=AsyncMock(return_value=invalid_plan),
        ):
            fallback = await self.service._plan_with_supervisor("审查合同并生成跟进任务", self.user.id)
        self.assertEqual(fallback["plan_source"], "rule_fallback")
        self.assertEqual(fallback["workers"], ["legal_compliance_agent", "workflow_agent"])
        self.assertEqual(fallback["fallback_reason"], "dependency 必须从前序 Worker 指向后序 Worker")

    async def test_unsupported_document_claim_blocks_task_creation(self):
        calls = [
            '{"thought":"提取风险","action_type":"tool_call","tool_name":"document_risk_tool","action_input":{"document_id":1}}',
            '{"thought":"知识分析完成","action_type":"finish","answer":"已提取风险，准备交接。"}',
        ]
        task_calls = []

        async def fake_chat(messages, stream=False, temperature=0.7):
            return calls.pop(0)

        fake_tools = {
            "document_risk_tool": FakeTool(
                "document_risk_tool", "提取风险", auto_context_fields=("user_id", "db"),
                parameters={"type": "object", "properties": {"document_id": {"type": "integer"}, "user_id": {"type": "integer"}}, "required": ["document_id", "user_id"]},
                handler=lambda **kwargs: tool_success("风险提取完成", {"document_id": 1, "risks": [{"title": "延期风险"}]}),
            ),
            "task_create_tool": FakeTool(
                "task_create_tool", "创建任务", auto_context_fields=("user_id", "db"),
                parameters={"type": "object", "properties": {"title": {"type": "string"}, "user_id": {"type": "integer"}}, "required": ["title", "user_id"]},
                handler=lambda **kwargs: task_calls.append(kwargs) or tool_success("任务创建完成", {"task": {"id": 1, "title": kwargs["title"]}}),
            ),
        }

        with patch("app.services.agent.agent_service.llm_service.chat", side_effect=fake_chat), patch.dict(
            "app.mcp.registry._TOOL_INSTANCES", fake_tools, clear=True,
        ):
            run = await self.service.run("提取文档 1 风险并创建任务", self.user.id, self.db, max_steps=4)

        self.assertEqual(run.status, "completed")
        self.assertIn("缺少原文证据", run.final_answer)
        self.assertEqual(task_calls, [])
        logs = self.service.get_run_logs(run.id, self.db, user_id=self.user.id)
        self.assertEqual([log.tool_name for log in logs], ["document_risk_tool", "evidence_verifier"])
        self.assertEqual(logs[-1].status, "error")
        self.assertEqual(json.loads(run.result)["evidence_verification"]["failed_claims"], 1)

    async def test_document_risk_flow_with_retry(self):
        calls = [
            "not-json",
            """
            {
              "thought": "先总结文档",
              "action_type": "tool_call",
              "tool_name": "document_summary_tool",
              "action_input": {"document_id": 5}
            }
            """,
            """
            {
              "thought": "提取风险点",
              "action_type": "tool_call",
              "tool_name": "document_risk_tool",
              "action_input": {"document_id": 5}
            }
            """,
            """
            {
              "thought": "总结与风险都已完成",
              "action_type": "finish",
              "answer": "已完成文档总结，并提取 1 条高风险事项。"
            }
            """,
        ]

        async def fake_chat(messages, stream=False, temperature=0.7):
            return calls.pop(0)

        fake_tools = {
            "document_summary_tool": FakeTool(
                "document_summary_tool",
                "文档总结",
                auto_context_fields=("user_id", "db"),
                parameters={
                    "type": "object",
                    "properties": {
                        "document_id": {"type": "integer"},
                        "user_id": {"type": "integer"},
                    },
                    "required": ["document_id", "user_id"],
                },
                handler=lambda **kwargs: tool_success(
                    "总结成功",
                    {"document_id": kwargs["document_id"], "summary": "项目交付时间紧张。"},
                ),
            ),
            "document_risk_tool": FakeTool(
                "document_risk_tool",
                "风险提取",
                auto_context_fields=("user_id", "db"),
                parameters={
                    "type": "object",
                    "properties": {
                        "document_id": {"type": "integer"},
                        "user_id": {"type": "integer"},
                    },
                    "required": ["document_id", "user_id"],
                },
                handler=lambda **kwargs: tool_success(
                    "风险提取成功",
                    {
                        "document_id": kwargs["document_id"],
                        "risks": [
                            {
                                "title": "交付延期",
                                "severity": "high",
                                "suggestion": "补充缓冲时间",
                                "evidence": "原文约定项目应在 6 月 30 日前完成交付。",
                            }
                        ],
                    },
                ),
            ),
        }

        with patch("app.services.agent.agent_service.llm_service.chat", side_effect=fake_chat), patch.dict(
            "app.mcp.registry._TOOL_INSTANCES",
            fake_tools,
            clear=True,
        ):
            run = await self.service.run("总结文档 5，并提取其中的风险点", self.user.id, self.db, max_steps=6)

        self.assertEqual(run.status, "completed")
        self.assertIn("高风险事项", run.final_answer)
        logs = self.service.get_run_logs(run.id, self.db, user_id=self.user.id)
        self.assertEqual(logs[0].tool_name, "retry")
        self.assertEqual(logs[0].status, "error")
        self.assertEqual(logs[1].tool_name, "document_summary_tool")
        self.assertEqual(logs[2].tool_name, "document_risk_tool")
        self.assertEqual(logs[3].tool_name, "evidence_verifier")
        self.assertEqual(logs[3].status, "success")
        serialized = self.service.serialize_run(run)
        self.assertEqual(serialized["artifacts"]["documents"][0]["document_id"], 5)

    async def test_preview_plan_returns_structured_steps(self):
        raw_preview = """
        {
          "summary": "先总结会议，再把行动项转成任务。",
          "estimated_steps": 2,
          "steps": [
            {
              "step": 1,
              "tool_name": "meeting_summary_tool",
              "purpose": "先拿到结构化会议纪要",
              "action_input_preview": {"meeting_id": 1}
            },
            {
              "step": 2,
              "tool_name": "meeting_action_tool",
              "purpose": "根据纪要提取任务并落库",
              "action_input_preview": {"meeting_id": 1}
            }
          ],
          "risks": ["如果会议不存在会失败"],
          "can_execute": true
        }
        """

        async def fake_generate(prompt, temperature=0.7):
            return raw_preview

        fake_tools = {
            "meeting_summary_tool": FakeTool("meeting_summary_tool", "总结会议"),
            "meeting_action_tool": FakeTool("meeting_action_tool", "创建任务"),
        }

        with patch("app.services.agent.agent_service.llm_service.generate", side_effect=fake_generate), patch.dict(
            "app.mcp.registry._TOOL_INSTANCES",
            fake_tools,
            clear=True,
        ):
            preview = await self.service.preview_plan("总结会议 1，并把行动项创建成任务", self.user.id, max_steps=5)

        self.assertEqual(preview["estimated_steps"], 2)
        self.assertTrue(preview["can_execute"])
        self.assertEqual(preview["steps"][0]["tool_name"], "meeting_summary_tool")
        self.assertEqual(preview["steps"][1]["tool_name"], "meeting_action_tool")
        self.assertIn("会议不存在", preview["risks"][0])

    async def test_preview_plan_uses_fixed_document_risk_demo_flow(self):
        preview = await self.service.preview_plan("总结文档 5，并提取其中的风险点", self.user.id, max_steps=5)

        self.assertTrue(preview["can_execute"])
        self.assertEqual(preview["estimated_steps"], 3)
        self.assertEqual(
            [step["tool_name"] for step in preview["steps"]],
            ["document_summary_tool", "document_risk_tool", "finish"],
        )
        self.assertEqual(preview["steps"][0]["action_input_preview"]["document_id"], 5)

    async def test_run_returns_partial_when_graph_reaches_max_steps(self):
        calls = [
            """
            {
              "thought": "先查任务",
              "action_type": "tool_call",
              "tool_name": "task_query_tool",
              "action_input": {"status": "todo"}
            }
            """,
            """
            {
              "thought": "继续查任务",
              "action_type": "tool_call",
              "tool_name": "task_query_tool",
              "action_input": {"status": "in_progress"}
            }
            """,
        ]

        async def fake_chat(messages, stream=False, temperature=0.7):
            return calls.pop(0)

        fake_tools = {
            "task_query_tool": FakeTool(
                "task_query_tool",
                "查询任务",
                auto_context_fields=("user_id", "db"),
                parameters={
                    "type": "object",
                    "properties": {
                        "status": {"type": "string"},
                        "user_id": {"type": "integer"},
                    },
                    "required": ["user_id"],
                },
                handler=lambda **kwargs: tool_success(
                    "查询成功",
                    {"tasks": [{"id": 1, "title": "任务 A", "status": kwargs.get("status")}]},
                ),
            ),
        }

        with patch("app.services.agent.agent_service.llm_service.chat", side_effect=fake_chat), patch.dict(
            "app.mcp.registry._TOOL_INSTANCES",
            fake_tools,
            clear=True,
        ):
            run = await self.service.run("查询任务状态", self.user.id, self.db, max_steps=2)

        self.assertEqual(run.status, "completed")
        self.assertEqual(run.total_steps, 2)
        self.assertIn("部分完成", run.final_answer)
        parsed_result = json.loads(run.result)
        self.assertEqual(parsed_result["agent_mode"], "langgraph_workflow")
        self.assertIn(parsed_result["workflow_engine"], {"langgraph", "internal_state_graph"})

    async def test_run_hides_internal_failure_detail(self):
        async def fake_chat(messages, stream=False, temperature=0.7):
            raise RuntimeError("db_password=secret")

        with patch("app.services.agent.agent_service.llm_service.chat", side_effect=fake_chat):
            run = await self.service.run("执行失败示例", self.user.id, self.db, max_steps=2)

        self.assertEqual(run.status, "error")
        self.assertEqual(run.error, "Agent 执行失败，请查看系统日志")
        self.assertEqual(run.failure_reason, "Agent 执行失败，请查看系统日志")
        serialized = self.service.serialize_run(run)
        self.assertEqual(serialized["error"], "Agent 执行失败，请查看系统日志")
        self.assertEqual(serialized["failure_reason"], "Agent 执行失败，请查看系统日志")
        self.assertNotIn("secret", json.dumps(serialized, ensure_ascii=False))

    async def test_retry_log_and_run_failure_reason_are_sanitized(self):
        calls = ["not-json", "not-json"]

        async def fake_chat(messages, stream=False, temperature=0.7):
            return calls.pop(0)

        with patch("app.services.agent.agent_service.llm_service.chat", side_effect=fake_chat):
            run = await self.service.run("触发重试", self.user.id, self.db, max_steps=1)

        logs = self.service.get_run_logs(run.id, self.db, user_id=self.user.id)
        self.assertEqual(logs[0].tool_name, "retry")
        self.assertEqual(self.service.serialize_log(logs[0])["error"], "Invalid JSON response")
        serialized = self.service.serialize_run(run)
        self.assertEqual(serialized["failure_reason"], "Invalid JSON response")

    async def test_run_can_resume_after_approval(self):
        calls = [
            """
            {
              "thought": "创建任务需要审批",
              "action_type": "tool_call",
              "tool_name": "task_create_tool",
              "action_input": {"title": "审批任务"}
            }
            """,
            """
            {
              "thought": "任务创建完成",
              "action_type": "finish",
              "answer": "任务已在审批后创建完成。"
            }
            """,
        ]

        worker_prompts = []

        async def fake_chat(messages, stream=False, temperature=0.7):
            worker_prompts.append(messages[0]["content"])
            return calls.pop(0)

        fake_tools = {
            "task_create_tool": FakeTool(
                "task_create_tool",
                "创建任务",
                auto_context_fields=("user_id", "db"),
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "user_id": {"type": "integer"},
                    },
                    "required": ["title", "user_id"],
                },
                handler=lambda **kwargs: tool_success(
                    "任务已创建",
                    {"task": {"id": 88, "title": kwargs["title"], "status": "todo"}},
                ),
            ),
        }

        with patch("app.services.agent.agent_service.llm_service.chat", side_effect=fake_chat), patch.dict(
            "app.mcp.registry._TOOL_INSTANCES",
            fake_tools,
            clear=True,
        ):
            paused_run = await self.service.run("创建一个审批任务", self.user.id, self.db, max_steps=3)
            self.assertEqual(paused_run.status, "awaiting_approval")
            approval = agent_approval_service.list_requests(db=self.db, user_id=self.user.id, status="pending")[0]
            self.assertEqual(approval.tool_name, "task_create_tool")
            logs = self.service.get_run_logs(paused_run.id, self.db, user_id=self.user.id)
            self.assertEqual(logs[0].status, "pending_approval")
            snapshot = json.loads(paused_run.workflow_state)
            self.assertEqual(snapshot["node"], "awaiting_approval")
            self.assertEqual(snapshot["worker_plan"], ["workflow_agent"])
            self.assertEqual(snapshot["current_tool_name"], "task_create_tool")
            self.assertEqual(snapshot["task_contract"]["protocol_version"], "agent_task_v1")
            self.assertEqual(snapshot["task_contract"]["receiver"], "workflow_agent")

            agent_approval_service.decide_request(
                db=self.db,
                approval_id=approval.id,
                user_id=self.user.id,
                approved=True,
                decision_note="allow",
            )
            # Recovery must prefer workflow_state over the legacy result payload.
            paused_run.result = json.dumps({"worker_agent": "document_agent", "max_steps": 3})
            self.db.add(paused_run)
            self.db.commit()
            resumed_run = await self.service.resume_after_approval(approval.id, self.user.id, self.db)

        self.assertEqual(resumed_run.status, "completed")
        self.assertIn("审批后创建完成", resumed_run.final_answer)
        resumed_logs = self.service.get_run_logs(resumed_run.id, self.db, user_id=self.user.id)
        self.assertEqual([log.tool_name for log in resumed_logs], ["task_create_tool", "task_create_tool", "finish"])
        self.assertEqual(resumed_logs[0].status, "approved")
        self.assertEqual(resumed_logs[1].status, "success")
        self.assertEqual(agent_approval_service.get_request(db=self.db, approval_id=approval.id, user_id=self.user.id).status, "executed")
        self.assertIn("workflow_agent", worker_prompts[-1])

    async def test_run_metrics_reports_role_and_tool_reliability_from_logs(self):
        now = datetime.utcnow()
        completed_run = AgentRun(
            user_id=self.user.id,
            goal="总结合同",
            status="completed",
            result=json.dumps({"supervisor_plan": {"workers": ["knowledge_agent"]}}),
            created_at=now - timedelta(seconds=2),
            completed_at=now,
        )
        failed_run = AgentRun(
            user_id=self.user.id,
            goal="创建任务",
            status="error",
            result=json.dumps({"supervisor_plan": {"workers": ["workflow_agent"]}}),
            created_at=now - timedelta(seconds=1),
            completed_at=now,
        )
        self.db.add_all([completed_run, failed_run])
        self.db.commit()
        self.db.refresh(completed_run)
        self.db.refresh(failed_run)
        self.db.add_all(
            [
                ToolCallLog(
                    agent_run_id=completed_run.id,
                    tool_name="document_summary_tool",
                    input_params=json.dumps({"_worker_agent": "knowledge_agent"}),
                    status="success",
                    duration_ms=120,
                ),
                ToolCallLog(
                    agent_run_id=failed_run.id,
                    tool_name="task_create_tool",
                    input_params=json.dumps({"_worker_agent": "workflow_agent"}),
                    status="error",
                    error="validation_error",
                    duration_ms=80,
                ),
                ToolCallLog(
                    agent_run_id=failed_run.id,
                    tool_name="retry",
                    input_params=json.dumps({"_worker_agent": "workflow_agent"}),
                    status="error",
                    duration_ms=0,
                ),
            ]
        )
        self.db.commit()

        metrics = self.service.get_run_metrics(db=self.db, user_id=self.user.id, days=30)
        by_agent = {item["agent_type"]: item for item in metrics["by_agent"]}

        self.assertEqual(by_agent["knowledge_agent"]["tool_success_rate"], 1.0)
        self.assertEqual(by_agent["workflow_agent"]["tool_success_rate"], 0.0)
        self.assertEqual(by_agent["workflow_agent"]["retry_count"], 1)
        self.assertEqual(metrics["reliability"]["tool_success_rate"], 0.5)
        self.assertEqual(metrics["reliability"]["routing_accuracy"], None)


if __name__ == "__main__":
    unittest.main()
