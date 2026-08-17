"""MCP Server — Task tools (create, query).

Run standalone::

    python -m app.mcp.servers.task_server

安全说明：独立 MCP 进程无 HTTP 鉴权上下文。本 server 统一经 ``tool_executor`` 执行，
缺少认证用户（user_id）时一律拒绝（fail-closed）；写工具（task_create）仍需人工审批。
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from app.core.database import SessionLocal
from app.mcp.executor import tool_executor

mcp = FastMCP(
    "task-server",
    instructions="Task management: create, query, filter.",
)


@mcp.tool(description="创建任务，可指定标题、描述、负责人、优先级和截止时间（需审批）。")
async def task_create(
    title: str,
    user_id: int | None = None,
    description: str = "",
    assignee: str | None = None,
    due_date: str | None = None,
    priority: str = "medium",
):
    if user_id is None:
        return str({"success": False, "message": "缺少认证上下文，工具调用被拒绝", "error": "unauthorized"})
    args = {"title": title, "description": description, "priority": priority}
    if assignee is not None:
        args["assignee"] = assignee
    if due_date is not None:
        args["due_date"] = due_date
    db = SessionLocal()
    try:
        out, _ = await tool_executor.execute(
            "task_create_tool", args, agent_type="workflow_agent", user_id=user_id, db=db,
        )
    finally:
        db.close()
    return str(out)


@mcp.tool(description="查询用户任务列表，可按状态筛选，适合后续汇总或生成催办邮件。")
async def task_query(user_id: int | None = None, status: str | None = None):
    if user_id is None:
        return str({"success": False, "message": "缺少认证上下文，工具调用被拒绝", "error": "unauthorized"})
    args = {}
    if status is not None:
        args["status"] = status
    db = SessionLocal()
    try:
        out, _ = await tool_executor.execute(
            "task_query_tool", args, agent_type="workflow_agent", user_id=user_id, db=db,
        )
    finally:
        db.close()
    return str(out)


if __name__ == "__main__":
    mcp.run()
