from app.services.email_service import email_service
from app.services.email_ai_service import email_ai_service
from app.tools.base import BaseAgentTool, tool_error, tool_success


class EmailTool(BaseAgentTool):
    name = "email_writer_tool"
    description = "根据收件对象、目的、核心信息和语气生成邮件草稿。"
    auto_context_fields = ("user_id", "db")
    parameters = {
        "type": "object",
        "properties": {
            "recipient": {"type": "string", "description": "收件对象，可选"},
            "purpose": {"type": "string", "description": "邮件目的，例如催办提醒、会议同步、风险汇报"},
            "key_points": {
                "type": "array",
                "items": {"type": "string"},
                "description": "邮件中必须覆盖的核心信息列表",
            },
            "tone": {
                "type": "string",
                "description": "邮件语气",
                "enum": ["professional", "casual", "friendly", "assertive"],
            },
            "need_action": {"type": "boolean", "description": "是否需要明确行动请求"},
            "user_id": {"type": "integer", "description": "当前用户 ID，由 Agent 自动注入"},
        },
        "required": ["purpose", "user_id"],
    }

    async def run(
        self,
        purpose: str,
        user_id: int,
        db,
        recipient: str | None = None,
        key_points: list[str] | None = None,
        tone: str = "professional",
        need_action: bool = False,
    ) -> dict:
        try:
            result = await email_service.generate(
                purpose=purpose,
                key_points=key_points or [],
                tone=tone,
                need_action=need_action,
                recipient=recipient,
                user_id=user_id,
                db=db,
            )
            draft = result["draft"]
            subjects = result["subject_candidates"]
            return tool_success(
                "邮件草稿已生成",
                {
                    "draft_id": draft.id,
                    "subject": draft.subject,
                    "subject_candidates": subjects,
                    "recipient": draft.recipient,
                    "purpose": purpose,
                    "key_points": key_points or [],
                    "tone": draft.tone,
                    "need_action": draft.need_action,
                    "content": draft.content,
                },
            )
        except Exception as e:
            return tool_error("邮件草稿生成失败", str(e), {"purpose": purpose})
