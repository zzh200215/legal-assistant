"""MCP Server — Email generation tool.

Run standalone::

    python -m app.mcp.servers.email_server
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from app.services.email_service import email_service

mcp = FastMCP(
    "email-server",
    instructions="AI-powered email draft generation.",
)


@mcp.tool(description="根据收件对象、目的、核心信息和语气生成邮件草稿。")
async def email_generate(
    purpose: str,
    user_id: int | None = None,
    recipient: str | None = None,
    key_points: list[str] | None = None,
    tone: str = "professional",
    need_action: bool = False,
) -> str:
    result = await email_service.generate(
        purpose=purpose,
        key_points=key_points or [],
        tone=tone,
        need_action=need_action,
        recipient=recipient,
        user_id=user_id,
        db=None,
    )
    draft = result["draft"]
    result_data = {
        "success": True,
        "message": "邮件草稿已生成",
        "data": {
            "draft_id": draft.id,
            "subject": draft.subject,
            "subject_candidates": result["subject_candidates"],
            "recipient": draft.recipient,
            "purpose": purpose,
            "tone": draft.tone,
            "need_action": draft.need_action,
            "content": draft.content,
        },
    }
    return str(result_data)


if __name__ == "__main__":
    mcp.run()
