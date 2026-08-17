import asyncio

from app.mcp.tool_contract import ToolContract
from app.services.jobs.task_service import task_service
from app.tools.base import BaseAgentTool, tool_error, tool_success


class TaskCreateTool(BaseAgentTool):
    name = "task_create_tool"
    description = "创建任务，可指定标题、描述、负责人、优先级和截止时间。"
    auto_context_fields = ("user_id", "db")
    contract = ToolContract(
        name="task_create_tool", read_only=False, requires_approval=True,
        side_effect="creates_task", idempotency_keyed=True, safely_retryable=False,
        cancellable=True, compensable=True, compensation_handler="compensate_task",
        audit_level="summary", sensitive_fields=("title", "description", "assignee"),
    )
    parameters = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "任务标题"},
            "description": {"type": "string", "description": "任务描述"},
            "assignee": {"type": "string", "description": "负责人姓名，可选"},
            "due_date": {"type": "string", "description": "截止时间，ISO 格式，可选"},
            "priority": {
                "type": "string",
                "description": "优先级",
                "enum": ["low", "medium", "high"],
            },
            "user_id": {"type": "integer", "description": "当前用户 ID，由 Agent 自动注入"},
        },
        "required": ["title", "user_id"],
    }

    async def run(
        self,
        title: str,
        user_id: int,
        db,
        description: str = "",
        assignee: str | None = None,
        due_date: str | None = None,
        priority: str = "medium",
    ) -> dict:
        try:
            task = await asyncio.to_thread(
                task_service.create,
                title=title,
                user_id=user_id,
                db=db,
                description=description,
                assignee=assignee,
                due_date=task_service._parse_due_date(due_date),
                priority=priority,
                source_type="agent",
            )
            return tool_success(
                f"任务已创建，ID: {task.id}",
                {
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
            )
        except Exception as e:
            return tool_error("任务创建失败", str(e), {"title": title})


class TaskQueryTool(BaseAgentTool):
    name = "task_query_tool"
    description = "查询用户任务列表，可按状态筛选，适合后续汇总或生成催办邮件。"
    auto_context_fields = ("user_id", "db")
    contract = ToolContract(
        name="task_query_tool", read_only=True, requires_approval=False,
        side_effect="reads_tasks", audit_level="summary",
    )
    parameters = {
        "type": "object",
        "properties": {
            "user_id": {"type": "integer", "description": "当前用户 ID，由 Agent 自动注入"},
            "status": {
                "type": "string",
                "description": "按状态筛选，可选",
                "enum": ["todo", "in_progress", "done", "cancelled"],
            },
        },
        "required": ["user_id"],
    }

    async def run(self, user_id: int, db, status: str | None = None) -> dict:
        try:
            tasks = await asyncio.to_thread(task_service.list_by_user, user_id, db, status=status)
            return tool_success(
                f"查询到 {len(tasks)} 条任务",
                {
                    "status_filter": status,
                    "tasks": [
                        {
                            "id": task.id,
                            "title": task.title,
                            "description": task.description,
                            "status": task.status,
                            "priority": task.priority,
                            "assignee": task.assignee,
                            "due_date": task.due_date.isoformat() if task.due_date else None,
                            "source_type": task.source_type,
                        }
                        for task in tasks
                    ],
                },
            )
        except Exception as e:
            return tool_error("任务查询失败", str(e), {"status": status})
