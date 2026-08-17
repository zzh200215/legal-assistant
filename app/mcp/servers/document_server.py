"""MCP Server — Document tools (search, summarise, extract risks).

Run standalone::

    python -m app.mcp.servers.document_server

安全说明：独立 MCP 进程无 HTTP 鉴权上下文。本 server 统一经 ``tool_executor`` 执行，
缺少认证用户（user_id）时一律拒绝（fail-closed），杜绝绕过统一执行链路的调用。
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from app.core.database import SessionLocal
from app.mcp.executor import tool_executor

mcp = FastMCP(
    "document-server",
    instructions="Document retrieval, summarisation, and risk extraction tools.",
)


@mcp.tool(description="根据问题检索文档知识库，返回相关文档片段，可选限定 document_id。")
async def document_search(query: str, document_id: int | None = None, user_id: int | None = None):
    if user_id is None:
        return str({"success": False, "message": "缺少认证上下文，工具调用被拒绝", "error": "unauthorized"})
    args = {"query": query}
    if document_id is not None:
        args["document_id"] = document_id
    db = SessionLocal()
    try:
        out, _ = await tool_executor.execute(
            "document_search_tool", args, agent_type="knowledge_agent", user_id=user_id, db=db,
        )
    finally:
        db.close()
    return str(out)


@mcp.tool(description="根据文档 ID 生成摘要，提取核心信息。")
async def document_summary(document_id: int, user_id: int | None = None, max_length: int = 500):
    if user_id is None:
        return str({"success": False, "message": "缺少认证上下文，工具调用被拒绝", "error": "unauthorized"})
    db = SessionLocal()
    try:
        out, _ = await tool_executor.execute(
            "document_summary_tool", {"document_id": document_id, "max_length": max_length},
            agent_type="knowledge_agent", user_id=user_id, db=db,
        )
    finally:
        db.close()
    return str(out)


@mcp.tool(description="根据文档 ID 提取结构化风险点，返回标题、说明、严重程度和建议动作。")
async def document_extract_risks(document_id: int, user_id: int | None = None):
    if user_id is None:
        return str({"success": False, "message": "缺少认证上下文，工具调用被拒绝", "error": "unauthorized"})
    db = SessionLocal()
    try:
        out, _ = await tool_executor.execute(
            "document_risk_tool", {"document_id": document_id},
            agent_type="knowledge_agent", user_id=user_id, db=db,
        )
    finally:
        db.close()
    return str(out)


if __name__ == "__main__":
    mcp.run()
