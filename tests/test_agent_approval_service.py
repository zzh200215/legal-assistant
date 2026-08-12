import unittest

from app.services.agent_approval_service import agent_approval_service


class AgentApprovalPolicyTests(unittest.TestCase):
    def test_write_and_sensitive_read_tools_require_approval(self):
        for tool_name in ("task_create_tool", "sql_query_tool"):
            with self.subTest(tool_name=tool_name):
                self.assertTrue(agent_approval_service.requires_approval(tool_name))

    def test_read_and_draft_tools_continue_without_approval(self):
        for tool_name in (
            "document_search_tool",
            "document_summary_tool",
            "document_risk_tool",
            "task_query_tool",
            "legal_consultation_tool",
            "legal_contract_review_tool",
            "legal_draft_tool",
        ):
            with self.subTest(tool_name=tool_name):
                self.assertFalse(agent_approval_service.requires_approval(tool_name))


if __name__ == "__main__":
    unittest.main()
