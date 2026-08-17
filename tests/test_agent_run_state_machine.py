"""Agent Run 状态机：合法/非法转移、取消、deadline、具类型状态序列化往返。"""

import unittest

from app.services.agent.agent_run_state import (
    AgentPlan,
    AgentRunState,
    IllegalRunTransition,
    RunStateMachine,
    STATUS_AWAITING_APPROVAL,
    STATUS_CANCELLED,
    STATUS_CANCELLING,
    STATUS_COMPLETED,
    STATUS_ERROR,
    STATUS_RUNNING,
)


class RunStateMachineTests(unittest.TestCase):
    def test_legal_transitions(self):
        self.assertEqual(RunStateMachine.transition(STATUS_RUNNING, STATUS_AWAITING_APPROVAL), STATUS_AWAITING_APPROVAL)
        self.assertEqual(RunStateMachine.transition(STATUS_RUNNING, STATUS_COMPLETED), STATUS_COMPLETED)
        self.assertEqual(RunStateMachine.transition(STATUS_RUNNING, STATUS_ERROR), STATUS_ERROR)
        self.assertEqual(RunStateMachine.transition(STATUS_RUNNING, STATUS_CANCELLING), STATUS_CANCELLING)
        self.assertEqual(RunStateMachine.transition(STATUS_AWAITING_APPROVAL, STATUS_RUNNING), STATUS_RUNNING)
        self.assertEqual(RunStateMachine.transition(STATUS_AWAITING_APPROVAL, STATUS_CANCELLED), STATUS_CANCELLED)
        self.assertEqual(RunStateMachine.transition(STATUS_CANCELLING, STATUS_CANCELLED), STATUS_CANCELLED)
        self.assertEqual(RunStateMachine.transition(STATUS_ERROR, STATUS_RUNNING), STATUS_RUNNING)

    def test_illegal_transitions_rejected(self):
        for status, target in (
            (STATUS_COMPLETED, STATUS_RUNNING),
            (STATUS_CANCELLED, STATUS_RUNNING),
            (STATUS_RUNNING, STATUS_CANCELLED),
            (STATUS_COMPLETED, STATUS_ERROR),
            (STATUS_CANCELLING, STATUS_RUNNING),
        ):
            with self.assertRaises(IllegalRunTransition, msg=f"{status}->{target}"):
                RunStateMachine.transition(status, target)

    def test_cancel_only_active(self):
        self.assertTrue(RunStateMachine.can_cancel(STATUS_RUNNING))
        self.assertTrue(RunStateMachine.can_cancel(STATUS_AWAITING_APPROVAL))
        self.assertFalse(RunStateMachine.can_cancel(STATUS_COMPLETED))
        self.assertFalse(RunStateMachine.can_cancel(STATUS_ERROR))


class AgentRunStateSerializationTests(unittest.TestCase):
    def test_snapshot_roundtrip_preserves_typed_state(self):
        plan = AgentPlan(
            intent="审查合同", workers=["legal_compliance_agent", "workflow_agent"],
            dependencies=[{"from": "legal_compliance_agent", "to": "workflow_agent"}],
            risk_level="medium", expected_artifacts=["document", "task"],
            execution_mode="sequential", rationale="r", plan_source="llm",
            requires_approval=True,
        )
        state = AgentRunState(
            run_id=7, user_id=3, status=STATUS_AWAITING_APPROVAL, node="awaiting_approval",
            step=2, trace_id="tr-1", organization_id=9, plan=plan,
            retry_count=1, cancel_requested=True,
        )
        restored = AgentRunState.from_snapshot(state.snapshot())
        self.assertIsNotNone(restored)
        self.assertEqual(restored.run_id, 7)
        self.assertEqual(restored.status, STATUS_AWAITING_APPROVAL)
        self.assertEqual(restored.plan.workers, ["legal_compliance_agent", "workflow_agent"])
        self.assertTrue(restored.plan.requires_approval)
        self.assertEqual(restored.retry_count, 1)
        self.assertTrue(restored.cancel_requested)

    def test_from_none_returns_none(self):
        self.assertIsNone(AgentRunState.from_snapshot(None))
        self.assertIsNone(AgentRunState.from_snapshot({}))


if __name__ == "__main__":
    unittest.main()
