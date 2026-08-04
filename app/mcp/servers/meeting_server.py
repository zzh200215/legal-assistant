"""MCP Server — Meeting tools (summarise, query, extract action items).

Run standalone::

    python -m app.mcp.servers.meeting_server
"""

from __future__ import annotations

import asyncio

from mcp.server.fastmcp import FastMCP

from app.services.meeting_service import meeting_service

mcp = FastMCP(
    "meeting-server",
    instructions="Meeting minutes generation, query, and action-item extraction.",
)


@mcp.tool(description="根据会议 ID 生成结构化会议纪要，包含主题、摘要、决策、行动项和风险。")
async def meeting_summary(meeting_id: int, user_id: int | None = None) -> str:
    summary = await meeting_service.summarize(meeting_id, db=None, user_id=user_id)
    payload = meeting_service.serialize_summary(summary)
    title = payload.get("theme") or (summary.meeting.title if summary.meeting else str(meeting_id))
    result = {
        "success": True,
        "message": f"会议《{title}》总结完成",
        "data": payload,
    }
    return str(result)


@mcp.tool(description="查询已有会议纪要和行动项，用于后续生成任务或邮件。")
async def meeting_query(meeting_id: int, user_id: int | None = None) -> str:
    summary = await asyncio.to_thread(meeting_service.get_summary, meeting_id, None, user_id=user_id)
    if not summary:
        result = {
            "success": False,
            "message": "会议纪要不存在",
            "data": {"meeting_id": meeting_id},
            "error": "Meeting summary not found",
        }
    else:
        result = {
            "success": True,
            "message": "会议纪要查询成功",
            "data": meeting_service.serialize_summary(summary),
        }
    return str(result)


@mcp.tool(description="从会议纪要中提取行动项并自动创建任务。")
async def meeting_extract_tasks(meeting_id: int, user_id: int | None = None) -> str:
    tasks = await asyncio.to_thread(meeting_service.extract_tasks, meeting_id, user_id, None)
    result = {
        "success": True,
        "message": f"从会议中提取并创建了 {len(tasks)} 条任务",
        "data": {
            "meeting_id": meeting_id,
            "tasks": [
                {
                    "id": t.id,
                    "title": t.title,
                    "assignee": t.assignee,
                    "priority": t.priority,
                    "status": t.status,
                }
                for t in tasks
            ],
        },
    }
    return str(result)


if __name__ == "__main__":
    mcp.run()
