import unittest
from unittest.mock import patch

from app.mcp.registry import MCPRegistry, mcp_registry
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


class MCPRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_call_tool_injects_context_and_fires_hooks_once(self):
        registry = MCPRegistry()
        fake_tool = FakeTool(
            "task_query_tool",
            "查询任务",
            auto_context_fields=("user_id", "db"),
            parameters={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "user_id": {"type": "integer"},
                    "db": {"type": "string"},
                    "token": {"type": "string"},
                },
                "required": ["status", "user_id"],
            },
        )

        before_calls = []
        after_calls = []
        registry.on_before_call(lambda tool_name, args, agent_type: before_calls.append((tool_name, args, agent_type)))
        registry.on_after_call(lambda tool_name, args, agent_type, result, duration_s: after_calls.append((tool_name, args, agent_type, result, duration_s)))

        with patch.dict("app.mcp.registry._TOOL_INSTANCES", {"task_query_tool": fake_tool}, clear=True):
            result = await registry.call_tool(
                "task_query_tool",
                {"status": "todo", "token": "secret-token"},
                agent_type="task_agent",
                user_id=42,
                db="db-session",
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["status"], "todo")
        self.assertEqual(result["data"]["user_id"], 42)
        self.assertEqual(result["data"]["db"], "db-session")
        self.assertEqual(len(before_calls), 1)
        self.assertEqual(len(after_calls), 1)
        self.assertEqual(before_calls[0][0], "task_query_tool")
        self.assertEqual(before_calls[0][2], "task_agent")
        self.assertEqual(before_calls[0][1]["token"], "****")
        self.assertEqual(before_calls[0][1]["db"], "****")

    async def test_call_tool_rejects_permission_denied(self):
        registry = MCPRegistry()
        fake_tool = FakeTool("sql_query_tool", "执行 SQL")

        with patch.dict("app.mcp.registry._TOOL_INSTANCES", {"sql_query_tool": fake_tool}, clear=True):
            result = await registry.call_tool(
                "sql_query_tool",
                {},
                agent_type="document_agent",
            )

        self.assertFalse(result["success"])
        self.assertEqual(result["mcp_error_code"], "MCP_PERMISSION_DENIED")
        self.assertEqual(result["data"]["agent_type"], "document_agent")
        self.assertEqual(result["data"]["requested_tool"], "sql_query_tool")

    async def test_call_tool_hides_internal_exception_detail(self):
        registry = MCPRegistry()

        class ExplodingTool(FakeTool):
            async def run(self, **kwargs):
                raise RuntimeError("db_password=secret")

        fake_tool = ExplodingTool(
            "task_query_tool",
            "查询任务",
            parameters={"type": "object", "properties": {}, "required": []},
        )

        with patch.dict("app.mcp.registry._TOOL_INSTANCES", {"task_query_tool": fake_tool}, clear=True):
            result = await registry.call_tool(
                "task_query_tool",
                {},
                agent_type="task_agent",
            )

        self.assertFalse(result["success"])
        self.assertEqual(result["mcp_error_code"], "MCP_INTERNAL_ERROR")
        self.assertEqual(result["error"], "Tool execution failed")
        self.assertNotIn("secret", str(result))

    def test_list_tools_for_returns_stable_sorted_order(self):
        registry = MCPRegistry()
        fake_tools = {
            "task_query_tool": FakeTool("task_query_tool", "查询任务"),
            "email_writer_tool": FakeTool("email_writer_tool", "写邮件"),
        }

        with patch.dict("app.mcp.registry._TOOL_INSTANCES", fake_tools, clear=True):
            tools = registry.list_tools_for("task_email_agent")

        self.assertEqual([item["name"] for item in tools], ["email_writer_tool", "task_query_tool"])

    def test_document_agent_can_discover_cross_document_conflict_tool(self):
        tool_names = [item["name"] for item in mcp_registry.list_tools_for("document_agent")]

        self.assertIn("document_conflict_tool", tool_names)


if __name__ == "__main__":
    unittest.main()
