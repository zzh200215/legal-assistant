"""MCP Server — Read-only SQL query tool.

Run standalone::

    python -m app.mcp.servers.data_server

安全说明：独立 MCP 进程无 HTTP 鉴权上下文。本 server 统一经 ``tool_executor`` 执行，
缺少认证用户（user_id）时一律拒绝（fail-closed），杜绝绕过统一执行链路的调用。
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from app.core.database import SessionLocal
from app.mcp.executor import tool_executor

mcp = FastMCP(
    "data-server",
    instructions="Read-only SQL queries against the application database.",
)


@mcp.tool(description="执行只读 SQL 查询，仅允许单条 SELECT，限制在白名单内。")
async def sql_query(sql: str, user_id: int | None = None):
    if user_id is None:
        return str({"success": False, "message": "缺少认证上下文，SQL 查询被拒绝", "error": "unauthorized"})
    db = SessionLocal()
    try:
        out, _ = await tool_executor.execute(
            "sql_query_tool", {"sql": sql},
            agent_type="general_agent", user_id=user_id, db=db,
        )
    finally:
        db.close()
    return str(out)


if __name__ == "__main__":
    mcp.run()
