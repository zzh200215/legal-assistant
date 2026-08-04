"""MCP Server — Read-only SQL query tool.

Run standalone::

    python -m app.mcp.servers.data_server
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from app.tools.sql_tool import SQLTool

mcp = FastMCP(
    "data-server",
    instructions="Read-only SQL queries against the application database.",
)

_tool = SQLTool()


@mcp.tool(description="执行只读 SQL 查询，仅允许 SELECT 语句。")
async def sql_query(sql: str) -> str:
    raw = await _tool.run(sql=sql)
    return str(raw)


if __name__ == "__main__":
    mcp.run()
