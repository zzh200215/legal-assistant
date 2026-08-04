"""MCP Server — Document tools (search, summarise, extract risks).

Run standalone::

    python -m app.mcp.servers.document_server

Or import and mount via the MCPRegistry in-process.
"""

from __future__ import annotations

import asyncio

from mcp.server.fastmcp import FastMCP

from app.services.analysis_service import analysis_service
from app.services.document_service import document_service
from app.services.rag_service import rag_service

mcp = FastMCP(
    "document-server",
    instructions="Document retrieval, summarisation, and risk extraction tools.",
)


@mcp.tool(description="根据问题检索文档知识库，返回相关文档片段，可选限定 document_id。")
async def document_search(
    query: str,
    document_id: int | None = None,
    user_id: int | None = None,
) -> str:
    """Search the document knowledge base by query string."""
    chunks = await asyncio.to_thread(rag_service.search, query, document_id, 5, user_id)
    result = {
        "success": True,
        "message": f"检索到 {len(chunks)} 条相关片段",
        "data": {"query": query, "document_id": document_id, "chunks": chunks},
    }
    return str(result)


@mcp.tool(description="根据文档 ID 生成摘要，提取核心信息。")
async def document_summary(
    document_id: int,
    user_id: int | None = None,
    max_length: int = 500,
) -> str:
    """Generate a summary for the given document."""
    raw_text = await asyncio.to_thread(document_service.summarize, document_id, None, user_id)
    summary = await analysis_service.summarize_document(raw_text, max_length=max_length)
    result = {
        "success": True,
        "message": "文档摘要已生成",
        "data": {"document_id": document_id, "summary": summary, "max_length": max_length},
    }
    return str(result)


@mcp.tool(description="根据文档 ID 提取结构化风险点，返回标题、说明、严重程度和建议动作。")
async def document_extract_risks(
    document_id: int,
    user_id: int | None = None,
) -> str:
    """Extract structured risk items from a document."""
    risks = await document_service.extract_risks(document_id, db=None, user_id=user_id)
    result = {
        "success": True,
        "message": f"提取到 {len(risks)} 条风险点",
        "data": {"document_id": document_id, "risks": risks},
    }
    return str(result)


if __name__ == "__main__":
    mcp.run()
