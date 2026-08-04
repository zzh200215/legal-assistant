import asyncio

from app.services.meeting_service import meeting_service
from app.tools.base import BaseAgentTool, tool_error, tool_success


class MeetingSummaryTool(BaseAgentTool):
    name = "meeting_summary_tool"
    description = "根据会议 ID 生成结构化会议纪要，包含主题、摘要、决策、行动项和风险。"
    auto_context_fields = ("user_id", "db")
    parameters = {
        "type": "object",
        "properties": {
            "meeting_id": {"type": "integer", "description": "会议记录 ID"},
            "user_id": {"type": "integer", "description": "当前用户 ID，由 Agent 自动注入"},
        },
        "required": ["meeting_id", "user_id"],
    }

    async def run(self, meeting_id: int, user_id: int, db) -> dict:
        try:
            summary = await meeting_service.summarize(meeting_id, db, user_id=user_id)
            payload = meeting_service.serialize_summary(summary)
            meeting_title = summary.meeting.title if summary.meeting else str(meeting_id)
            return tool_success(
                f"会议《{payload.get('theme') or meeting_title}》总结完成",
                payload,
            )
        except Exception as e:
            return tool_error("会议纪要生成失败", str(e), {"meeting_id": meeting_id})


class MeetingQueryTool(BaseAgentTool):
    name = "meeting_query_tool"
    description = "查询已有会议纪要和行动项，用于后续生成任务或邮件。"
    auto_context_fields = ("user_id", "db")
    parameters = {
        "type": "object",
        "properties": {
            "meeting_id": {"type": "integer", "description": "会议记录 ID"},
            "user_id": {"type": "integer", "description": "当前用户 ID，由 Agent 自动注入"},
        },
        "required": ["meeting_id", "user_id"],
    }

    async def run(self, meeting_id: int, user_id: int, db) -> dict:
        try:
            summary = await asyncio.to_thread(meeting_service.get_summary, meeting_id, db, user_id=user_id)
            if not summary:
                return tool_error("会议纪要不存在", "Meeting summary not found", {"meeting_id": meeting_id})
            return tool_success("会议纪要查询成功", meeting_service.serialize_summary(summary))
        except Exception as e:
            return tool_error("会议纪要查询失败", str(e), {"meeting_id": meeting_id})


class MeetingActionTool(BaseAgentTool):
    name = "meeting_action_tool"
    description = "从会议纪要中提取行动项并自动创建任务。"
    auto_context_fields = ("user_id", "db")
    parameters = {
        "type": "object",
        "properties": {
            "meeting_id": {"type": "integer", "description": "会议记录 ID"},
            "user_id": {"type": "integer", "description": "当前用户 ID，由 Agent 自动注入"},
        },
        "required": ["meeting_id", "user_id"],
    }

    async def run(self, meeting_id: int, db, user_id: int) -> dict:
        try:
            tasks = await asyncio.to_thread(meeting_service.extract_tasks, meeting_id, user_id, db)
            return tool_success(
                f"从会议中提取并创建了 {len(tasks)} 条任务",
                {
                    "meeting_id": meeting_id,
                    "tasks": [
                        {
                            "id": task.id,
                            "title": task.title,
                            "assignee": task.assignee,
                            "priority": task.priority,
                            "status": task.status,
                        }
                        for task in tasks
                    ],
                },
            )
        except Exception as e:
            return tool_error("会议待办创建失败", str(e), {"meeting_id": meeting_id})
