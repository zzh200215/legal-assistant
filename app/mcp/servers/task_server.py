"""MCP Server — Task tools (create, query).

Run standalone::

    python -m app.mcp.servers.task_server
"""

from __future__ import annotations

import asyncio

from mcp.server.fastmcp import FastMCP

from app.services.task_service import task_service

mcp = FastMCP(
    "task-server",
    instructions="Task management: create, query, filter.",
)


@mcp.tool(description="创建任务，可指定标题、描述、负责人、优先级和截止时间。")
async def task_create(
    title: str,
    user_id: int | None = None,
    description: str = "",
    assignee: str | None = None,
    due_date: str | None = None,
    priority: str = "medium",
) -> str:
    task = await asyncio.to_thread(
        task_service.create,
        title=title,
        user_id=user_id,
        db=None,
        description=description,
        assignee=assignee,
        due_date=task_service._parse_due_date(due_date) if due_date else None,
        priority=priority,
        source_type="agent",
    )
    result = {
        "success": True,
        "message": f"任务已创建，ID: {task.id}",
        "data": {
            "task": {
                "id": task.id,
                "title": task.title,
                "description": task.description,
                "assignee": task.assignee,
                "priority": task.priority,
                "status": task.status,
                "due_date": task.due_date.isoformat() if task.due_date else None,
            }
        },
    }
    return str(result)


@mcp.tool(description="查询用户任务列表，可按状态筛选，适合后续汇总或生成催办邮件。")
async def task_query(
    user_id: int | None = None,
    status: str | None = None,
) -> str:
    tasks = await asyncio.to_thread(task_service.list_by_user, user_id, None, status=status)
    result = {
        "success": True,
        "message": f"查询到 {len(tasks)} 条任务",
        "data": {
            "status_filter": status,
            "tasks": [
                {
                    "id": t.id,
                    "title": t.title,
                    "description": t.description,
                    "status": t.status,
                    "priority": t.priority,
                    "assignee": t.assignee,
                    "due_date": t.due_date.isoformat() if t.due_date else None,
                    "source_type": t.source_type,
                }
                for t in tasks
            ],
        },
    }
    return str(result)


if __name__ == "__main__":
    mcp.run()
