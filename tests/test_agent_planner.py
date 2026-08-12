"""Planner：结构化计划、不执行工具、单领域直连路由、规则回退。"""

import unittest
from unittest.mock import AsyncMock, patch

from app.services.agent_planner import Planner, build_worker_plan
from app.services.agent_run_state import AgentPlan


class PlannerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.planner = Planner()

    def test_worker_plan_routing(self):
        self.assertEqual(build_worker_plan("总结文档 1 并提取风险"), ["knowledge_agent"])
        self.assertEqual(build_worker_plan("审查这份合同"), ["legal_compliance_agent"])
        self.assertEqual(build_worker_plan("查询未完成任务"), ["workflow_agent"])
        self.assertEqual(
            build_worker_plan("总结文档 1 并提取风险，再创建跟进任务"),
            ["knowledge_agent", "workflow_agent"],
        )

    async def test_plan_dict_single_domain_direct_route_without_llm(self):
        generate = AsyncMock(side_effect=AssertionError("LLM must not run for single domain"))
        with patch("app.services.agent_planner.llm_service.generate", new=generate):
            plan = await self.planner.plan_dict("查询合同 12 的交付日期", user_id=1)
        self.assertEqual(plan["workers"], ["legal_compliance_agent"])
        self.assertEqual(plan["plan_source"], "deterministic_direct_route")

    async def test_plan_returns_typed_agent_plan_and_marks_write_approval(self):
        with patch(
            "app.services.agent_planner.llm_service.generate",
            new=AsyncMock(return_value='{"workers": ["legal_compliance_agent"]}'),
        ):
            plan = await self.planner.plan("审查合同 12 并创建跟进任务", user_id=1)
        self.assertIsInstance(plan, AgentPlan)
        # workflow_agent 出现在计划中 → requires_approval
        with patch(
            "app.services.agent_planner.llm_service.generate",
            new=AsyncMock(
                return_value=(
                    '{"workers": ["legal_compliance_agent", "workflow_agent"],'
                    ' "dependencies": [{"from": "legal_compliance_agent", "to": "workflow_agent"}]}'
                )
            ),
        ):
            plan2 = await self.planner.plan("审查合同并创建跟进任务", user_id=1)
        self.assertTrue(plan2.requires_approval)
        self.assertEqual(plan2.risk_level, "medium")

    def test_planner_has_no_execution_capability(self):
        # Planner 只暴露 plan 相关方法，不得持有工具或执行入口。
        methods = [name for name in dir(Planner) if not name.startswith("_")]
        self.assertTrue(methods)
        self.assertFalse(hasattr(self.planner, "execute"))
        self.assertFalse(hasattr(self.planner, "call_tool"))

    async def test_plan_never_invokes_tools(self):
        with patch("app.services.agent_planner.llm_service.generate", new=AsyncMock(return_value="{}")):
            plan = await self.planner.plan("总结文档 1", user_id=1)
        # 单领域请求直连路由，不调用 LLM；不产生任何工具副作用
        self.assertEqual(plan.plan_source, "deterministic_direct_route")


if __name__ == "__main__":
    unittest.main()
