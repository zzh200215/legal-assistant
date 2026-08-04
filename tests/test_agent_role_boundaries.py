import unittest
from unittest.mock import AsyncMock, patch

from app.mcp.permissions import agent_allows_tool, canonical_agent_type
from app.services.agent_registry import AGENT_REGISTRY_VERSION, TASK_PROTOCOL_VERSION, list_agent_registrations
from app.services.agent_service import AgentService, POLICY_GUARDRAIL_ROLE, SUB_AGENTS


class AgentRoleBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = AgentService()

    def test_expert_agents_are_runtime_planning_roles(self):
        self.assertEqual(
            set(SUB_AGENTS),
            {
                "knowledge_agent", "meeting_agent", "data_agent", "project_agent",
                "legal_compliance_agent", "communication_agent", "workflow_agent",
            },
        )
        self.assertEqual(POLICY_GUARDRAIL_ROLE, "policy_guardrail")

    def test_agent_registry_exposes_canonical_contracts_and_live_acl(self):
        registrations = list_agent_registrations()
        self.assertEqual(AGENT_REGISTRY_VERSION, "enterprise_experts_v1")
        self.assertEqual(TASK_PROTOCOL_VERSION, "agent_task_v1")
        self.assertEqual(
            [item["agent_type"] for item in registrations],
            ["knowledge_agent", "meeting_agent", "data_agent", "project_agent", "legal_compliance_agent", "communication_agent", "workflow_agent"],
        )
        meeting = registrations[1]
        self.assertIn("meeting_summary_tool", meeting["allowed_tools"])
        self.assertNotIn("meeting_action_tool", meeting["allowed_tools"])

    def test_meeting_agent_cannot_execute_business_side_effects(self):
        self.assertTrue(agent_allows_tool("meeting_agent", "meeting_summary_tool"))
        self.assertFalse(agent_allows_tool("meeting_agent", "meeting_action_tool"))
        self.assertFalse(agent_allows_tool("meeting_agent", "task_create_tool"))
        self.assertTrue(agent_allows_tool("workflow_agent", "meeting_action_tool"))
        self.assertTrue(agent_allows_tool("workflow_agent", "task_create_tool"))
        self.assertTrue(agent_allows_tool("communication_agent", "email_writer_tool"))
        self.assertFalse(agent_allows_tool("communication_agent", "task_create_tool"))
        self.assertTrue(agent_allows_tool("legal_compliance_agent", "document_risk_tool"))
        self.assertFalse(agent_allows_tool("legal_compliance_agent", "email_writer_tool"))

    def test_legacy_worker_names_normalize_to_current_roles(self):
        self.assertEqual(canonical_agent_type("document_agent"), "knowledge_agent")
        self.assertEqual(canonical_agent_type("task_agent"), "workflow_agent")
        plan, error = self.service._validate_supervisor_plan(
            {
                "intent": "总结文档后生成邮件",
                "workers": ["document_agent", "communication_agent"],
                "dependencies": [{"from": "document_agent", "to": "communication_agent"}],
                "risk_level": "medium",
                "expected_artifacts": ["document", "email"],
            }
        )
        self.assertIsNone(error)
        self.assertEqual(plan["workers"], ["knowledge_agent", "communication_agent"])
        self.assertEqual(
            plan["dependencies"],
            [{"from": "knowledge_agent", "to": "communication_agent"}],
        )

    def test_domain_plans_keep_analysis_before_workflow_actions(self):
        self.assertEqual(
            self.service._build_supervisor_worker_plan("总结合同并生成通知邮件"),
            ["legal_compliance_agent", "communication_agent"],
        )
        self.assertEqual(
            self.service._build_supervisor_worker_plan("总结会议并创建任务"),
            ["meeting_agent", "workflow_agent"],
        )
        self.assertEqual(
            self.service._build_supervisor_worker_plan("生成本周销售日报"),
            ["data_agent"],
        )
        self.assertEqual(
            self.service._build_supervisor_worker_plan("评估项目延期风险并生成周报"),
            ["project_agent"],
        )

    async def test_single_domain_request_uses_direct_route_without_supervisor_llm(self):
        generate = AsyncMock(side_effect=AssertionError("Supervisor LLM must not run for a single domain"))
        with patch("app.services.agent_service.llm_service.generate", new=generate):
            plan = await self.service._plan_with_supervisor("查询合同 12 的交付日期", user_id=1)
        generate.assert_not_awaited()
        self.assertEqual(plan["workers"], ["legal_compliance_agent"])
        self.assertEqual(plan["plan_source"], "deterministic_direct_route")


if __name__ == "__main__":
    unittest.main()
